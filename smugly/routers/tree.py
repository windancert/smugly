import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from smugly import config
from smugly.db import get_conn, init_db
from smugly.scanner import Scanner
from smugly.smugmug import SmugMugClient

router = APIRouter()
templates = Jinja2Templates(directory="smugly/templates")
_scan_lock = threading.Lock()


def _make_client() -> SmugMugClient | None:
    s = config.settings
    if not s.smugmug_api_key or not s.smugmug_api_secret:
        return None
    tokens = config.load_tokens()
    if not tokens.get("oauth_token"):
        return None
    return SmugMugClient(
        s.smugmug_api_key, s.smugmug_api_secret,
        tokens["oauth_token"], tokens["oauth_token_secret"],
    )


def _build_tree_rows(db_path: Path, parent: str = "") -> list[dict]:
    """
    Build unified dual-tree rows for display.
    Each row: {path, name, depth, local_status, remote_node_id, is_gallery, error}
    """
    conn = get_conn(db_path)

    # Load all albums (both folder and gallery nodes)
    albums: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM albums"):
        albums[row["local_path"]] = dict(row)

    # Load file statuses grouped by directory
    dir_statuses: dict[str, set] = {}
    for row in conn.execute("SELECT local_path, status FROM files"):
        path = row["local_path"]
        if path.endswith("/") or path == "__root__":
            continue
        parts = path.rsplit("/", 1)
        dir_path = parts[0] if len(parts) == 2 else ""
        dir_statuses.setdefault(dir_path, set()).add(row["status"])

    # Collect all known directory paths from files and albums
    all_dirs: set[str] = set(albums.keys())
    for path in dir_statuses:
        all_dirs.add(path)

    # Use the snapshot written by the scanner; fall back to os.walk before first scan
    photo_dir = config.settings.photo_dir
    local_dirs: set[str] = set()
    for row in conn.execute("SELECT path FROM local_dirs"):
        local_dirs.add(row["path"])

    if not local_dirs and photo_dir.exists():
        for dirpath, dirnames, _ in __import__("os").walk(photo_dir):
            abs_dir = Path(dirpath)
            rel = abs_dir.relative_to(photo_dir).as_posix()
            if rel == ".":
                rel = ""
            local_dirs.add(rel)
            dirnames[:] = [
                d for d in dirnames
                if d not in {"_deleted"} and d != db_path.name
            ]

    all_dirs |= local_dirs

    # Build rows filtered to parent
    rows = []
    for dir_path in sorted(all_dirs):
        if dir_path == "":
            continue
        parts = dir_path.split("/")
        immediate_parent = "/".join(parts[:-1])
        if immediate_parent != parent:
            continue

        depth = len(parts)
        name = parts[-1]
        album = albums.get(dir_path)
        statuses = dir_statuses.get(dir_path, set())

        # Aggregate status for the row
        if "structure_error" in statuses or "too_deep" in statuses:
            agg_status = "structure_error"
        elif "conflict" in statuses:
            agg_status = "conflict"
        elif statuses == {"in_sync"}:
            agg_status = "in_sync"
        elif "local_only" in statuses and not statuses - {"local_only", "in_sync"}:
            agg_status = "local_only"
        elif "remote_only" in statuses and not statuses - {"remote_only", "in_sync"}:
            agg_status = "remote_only"
        elif statuses:
            agg_status = "local_only"
        else:
            agg_status = "unknown"

        local_exists = dir_path in local_dirs
        remote_exists = album is not None

        # Has children = has subdirectories
        has_children = any(
            d.startswith(dir_path + "/") and d.count("/") == dir_path.count("/") + 1
            for d in all_dirs
        )

        rows.append({
            "path": dir_path,
            "name": name,
            "depth": depth,
            "status": agg_status,
            "local_exists": local_exists,
            "remote_exists": remote_exists,
            "is_gallery": album["node_type"] == "gallery" if album else bool(statuses),
            "has_children": has_children,
        })

    return rows


@router.get("/tree", response_class=HTMLResponse)
async def tree_page(request: Request):
    db_path = config.get_db_path()
    if db_path.exists():
        init_db(db_path)
        rows = _build_tree_rows(db_path, parent="")
    else:
        rows = []

    conn = get_conn(db_path) if db_path.exists() else None
    scan_state = {}
    if conn:
        row = conn.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
        if row:
            scan_state = dict(row)

    return templates.TemplateResponse("tree.html", {
        "request": request,
        "rows": rows,
        "scan_state": scan_state,
        "photo_dir": str(config.settings.photo_dir),
    })


@router.get("/tree/children", response_class=HTMLResponse)
async def tree_children(request: Request, path: str = ""):
    db_path = config.get_db_path()
    init_db(db_path)
    rows = _build_tree_rows(db_path, parent=path)
    return templates.TemplateResponse("_tree_rows.html", {
        "request": request,
        "rows": rows,
    })


@router.post("/scan")
async def trigger_scan(background_tasks: BackgroundTasks, subpath: str = ""):
    if not _scan_lock.acquire(blocking=False):
        return HTMLResponse("Scan already running", status_code=409)
    background_tasks.add_task(_run_scan, subpath)
    return HTMLResponse(
        '<div class="text-sm text-blue-600">Scan started…</div>',
        status_code=202,
        headers={"HX-Trigger": "treeRefresh"},
    )


@router.get("/scan/status", response_class=HTMLResponse)
async def scan_status(request: Request):
    db_path = config.get_db_path()
    if not db_path.exists():
        return HTMLResponse("")
    init_db(db_path)
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
    state = dict(row) if row else {}

    is_scanning = bool(state.get("is_scanning"))
    recently_done = False
    if not is_scanning and state.get("last_scan_at"):
        t = datetime.fromisoformat(state["last_scan_at"]).replace(tzinfo=timezone.utc)
        recently_done = (datetime.now(timezone.utc) - t) < timedelta(seconds=5)
    headers = {"HX-Trigger": "treeRefresh"} if (is_scanning or recently_done) else {}

    return templates.TemplateResponse("_scan_status.html", {
        "request": request,
        "scan_state": state,
    }, headers=headers)


def _run_scan(subpath: str) -> None:
    try:
        db_path = config.get_db_path()
        init_db(db_path)
        client = _make_client()
        scanner = Scanner(db_path, config.settings.photo_dir, client)
        scanner.scan(subpath)
    finally:
        _scan_lock.release()
