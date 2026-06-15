from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from smugly import config
from smugly.db import get_conn, init_db
from smugly import worker

router = APIRouter(prefix="/queue")
templates = Jinja2Templates(directory="smugly/templates")


@router.get("", response_class=HTMLResponse)
async def queue_page(request: Request):
    db_path = config.get_db_path()
    init_db(db_path)
    conn = get_conn(db_path)
    items = [
        dict(r) for r in conn.execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY created_at"
        )
    ]
    wstate = conn.execute("SELECT status FROM worker_state WHERE id=1").fetchone()
    return templates.TemplateResponse("queue.html", {
        "request": request,
        "items": items,
        "worker_status": wstate["status"] if wstate else "stopped",
    })


@router.get("/status", response_class=HTMLResponse)
async def queue_status(request: Request):
    db_path = config.get_db_path()
    if not db_path.exists():
        return HTMLResponse("")
    init_db(db_path)
    conn = get_conn(db_path)
    counts = dict(conn.execute(
        "SELECT action, COUNT(*) as n FROM queue WHERE status='pending' GROUP BY action"
    ).fetchall() or [])
    total = sum(counts.values())
    wstate = conn.execute("SELECT status FROM worker_state WHERE id=1").fetchone()
    return templates.TemplateResponse("_queue_status.html", {
        "request": request,
        "counts": counts,
        "total": total,
        "worker_status": wstate["status"] if wstate else "stopped",
    })


@router.delete("/{item_id}", response_class=HTMLResponse)
async def cancel_item(item_id: int):
    db_path = config.get_db_path()
    conn = get_conn(db_path)
    conn.execute(
        "UPDATE queue SET status='cancelled', updated_at=datetime('now') WHERE id=? AND status='pending'",
        (item_id,),
    )
    conn.commit()
    return HTMLResponse("")  # HTMX removes the row


@router.post("/worker/pause")
async def pause_worker():
    worker.pause(config.get_db_path())
    return HTMLResponse(_worker_controls("paused"))


@router.post("/worker/resume")
async def resume_worker():
    worker.resume(config.get_db_path())
    return HTMLResponse(_worker_controls("running"))


@router.post("/worker/stop")
async def stop_worker():
    worker.stop(config.get_db_path())
    return HTMLResponse(_worker_controls("stopped"))


def _worker_controls(status: str) -> str:
    if status == "running":
        return (
            '<span class="text-green-600 font-medium">Running</span>'
            ' <button hx-post="/queue/worker/pause" hx-target="#worker-controls" hx-swap="outerHTML"'
            ' class="ml-2 px-2 py-1 text-xs bg-yellow-100 rounded">Pause</button>'
            ' <button hx-post="/queue/worker/stop" hx-target="#worker-controls" hx-swap="outerHTML"'
            ' class="ml-1 px-2 py-1 text-xs bg-red-100 rounded">Stop</button>'
        )
    if status == "paused":
        return (
            '<span class="text-yellow-600 font-medium">Paused</span>'
            ' <button hx-post="/queue/worker/resume" hx-target="#worker-controls" hx-swap="outerHTML"'
            ' class="ml-2 px-2 py-1 text-xs bg-green-100 rounded">Resume</button>'
            ' <button hx-post="/queue/worker/stop" hx-target="#worker-controls" hx-swap="outerHTML"'
            ' class="ml-1 px-2 py-1 text-xs bg-red-100 rounded">Stop</button>'
        )
    return (
        '<span class="text-gray-500 font-medium">Stopped</span>'
        ' <button hx-post="/queue/worker/resume" hx-target="#worker-controls" hx-swap="outerHTML"'
        ' class="ml-2 px-2 py-1 text-xs bg-green-100 rounded">Start</button>'
    )
