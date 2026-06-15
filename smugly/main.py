from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from smugly import config, worker
from smugly.db import init_db
from smugly.routers import queue, settings, tree


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = config.get_db_path()
    init_db(db_path)
    worker.start(db_path)
    yield
    worker.stop(db_path)


app = FastAPI(title="Smugly", lifespan=lifespan)
templates = Jinja2Templates(directory="smugly/templates")

app.include_router(tree.router)
app.include_router(queue.router)
app.include_router(settings.router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db_path = config.get_db_path()
    init_db(db_path)

    from smugly.db import get_conn
    conn = get_conn(db_path)

    scan_row = conn.execute("SELECT * FROM scan_state WHERE id=1").fetchone()
    worker_row = conn.execute("SELECT status FROM worker_state WHERE id=1").fetchone()

    counts = {
        r["action"]: r["n"]
        for r in conn.execute(
            "SELECT action, COUNT(*) as n FROM queue WHERE status='pending' GROUP BY action"
        )
    }
    conflict_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE status='conflict'"
    ).fetchone()[0]
    error_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE status='structure_error'"
    ).fetchone()[0]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "scan_state": dict(scan_row) if scan_row else {},
        "worker_status": worker_row["status"] if worker_row else "stopped",
        "queue_counts": counts,
        "conflict_count": conflict_count,
        "error_count": error_count,
        "photo_dir": str(config.settings.photo_dir),
    })
