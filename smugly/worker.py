import threading
from pathlib import Path

# Phase 1: worker thread exists and manages pause/stop state,
# but does not execute queue items yet.

pause_event = threading.Event()
stop_event = threading.Event()

pause_event.set()  # not paused by default
_thread: threading.Thread | None = None


def _run(db_path: Path) -> None:
    from smugly.db import get_conn
    conn = get_conn(db_path)
    conn.execute("UPDATE worker_state SET status='running' WHERE id=1")
    conn.commit()

    while not stop_event.is_set():
        pause_event.wait()
        if stop_event.is_set():
            break
        # Phase 2: pick next pending queue item and execute it here.
        stop_event.wait(timeout=1)

    conn.execute("UPDATE worker_state SET status='stopped' WHERE id=1")
    conn.commit()


def start(db_path: Path) -> None:
    global _thread
    stop_event.clear()
    pause_event.set()
    _thread = threading.Thread(target=_run, args=(db_path,), daemon=True, name="smugly-worker")
    _thread.start()


def pause(db_path: Path) -> None:
    from smugly.db import get_conn
    pause_event.clear()
    conn = get_conn(db_path)
    conn.execute("UPDATE worker_state SET status='paused' WHERE id=1")
    conn.commit()


def resume(db_path: Path) -> None:
    from smugly.db import get_conn
    pause_event.set()
    conn = get_conn(db_path)
    conn.execute("UPDATE worker_state SET status='running' WHERE id=1")
    conn.commit()


def stop(db_path: Path) -> None:
    stop_event.set()
    pause_event.set()  # unblock if paused so thread can exit
