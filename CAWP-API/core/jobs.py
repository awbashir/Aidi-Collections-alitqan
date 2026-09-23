"""In-memory background job queue.

Heavy operations (Excel imports of thousands of clients, big exports, report builds) used to
run inside the HTTP request, holding a gunicorn thread for minutes and timing out the browser.
They now run on a small in-process thread pool; job state lives in Redis so any API instance /
thread can answer `GET /jobs/<id>`, and results survive a process restart for JOB_RESULT_TTL.

Usage:
    job_id = jobs.submit("import-clients", do_import, file_path, user_id)
    ...
    jobs.get(job_id) -> {"id", "name", "status": queued|running|done|failed, "progress": 0-100,
                         "message", "result", "error", "created_at", "finished_at"}
Inside the worker, call jobs.progress(job_id, pct, message) to report progress.
"""
import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from core import cache
from core.config import Config

log = logging.getLogger("cawp.jobs")

_executor = None
_executor_lock = threading.Lock()
_app = None  # Flask app, for app_context in workers
_local_progress = threading.local()


def init_app(app):
    global _app
    _app = app


def _pool():
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=Config.JOB_WORKERS, thread_name_prefix="job")
    return _executor


def _key(job_id):
    return f"job:{job_id}"


def _save(job):
    cache.set(_key(job["id"]), job, ttl=Config.JOB_RESULT_TTL)


def get(job_id):
    return cache.get(_key(job_id))


def progress(job_id, pct=None, message=None, **extra):
    job = get(job_id)
    if not job:
        return
    if pct is not None:
        job["progress"] = max(0, min(100, int(pct)))
    if message is not None:
        job["message"] = message
    job.update(extra)
    _save(job)


def submit(name, fn, *args, owner=None, **kwargs):
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "name": name,
        "owner": str(owner) if owner is not None else None,
        "status": "queued",
        "progress": 0,
        "message": None,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
    }
    _save(job)

    def run():
        j = get(job_id) or job
        j.update(status="running", started_at=time.time())
        _save(j)
        try:
            if _app is not None:
                with _app.app_context():
                    result = fn(job_id, *args, **kwargs)
            else:
                result = fn(job_id, *args, **kwargs)
            j = get(job_id) or j
            j.update(status="done", progress=100, result=result, finished_at=time.time())
        except Exception as e:  # noqa: BLE001
            log.error("job %s (%s) failed: %s\n%s", job_id, name, e, traceback.format_exc())
            j = get(job_id) or j
            j.update(status="failed", error=str(e), finished_at=time.time())
        _save(j)

    _pool().submit(run)
    return job_id


def stats():
    ex = _executor
    return {
        "workers": Config.JOB_WORKERS,
        "queued": ex._work_queue.qsize() if ex is not None else 0,  # noqa: SLF001
    }
