"""GUI-thread task dispatch for the FreeCAD MCP addon.

XML-RPC handler threads must not touch FreeCAD documents directly; they hand
tasks to the GUI thread, which drains them on the QTimer heartbeat
(``process_gui_tasks`` in ``rpc_server``). This module is deliberately free
of FreeCAD/Qt imports so the dispatch semantics are unit-testable headlessly.

Ported from upstream neka-nat/freecad-mcp (PR #87, commit 2cd4fd8): each
dispatch owns a single-slot response queue and a cancel event. On timeout the
queued task is cancelled — if it has not started yet it will never run, so a
caller retrying after a timeout cannot trigger a double execution (duplicate
``create_object``, double ``execute_code`` mutation). A task already running
on the GUI thread cannot be interrupted; only its result is discarded.

The per-call response queue also replaces the legacy shared global response
queue, whose single slot let a late result from one call be consumed as the
answer to the next.
"""

import queue
import threading
import traceback

DEFAULT_TIMEOUT = 60.0

_task_queue: "queue.Queue" = queue.Queue()


def drain_pending() -> None:
    """Run every queued task. Must only be called on the GUI thread."""
    while not _task_queue.empty():
        task = _task_queue.get()
        task()


def dispatch_to_gui(task, timeout: float = DEFAULT_TIMEOUT):
    """Run ``task`` on the GUI thread and return its result.

    Returns the task's return value, or an error string if the task raises
    or the wait times out (the legacy GUI-handler contract: success value or
    error ``str``). A timed-out task that has not started is cancelled and
    never runs.
    """
    response_queue: "queue.Queue" = queue.Queue(maxsize=1)
    cancelled = threading.Event()

    def _wrapped() -> None:
        if cancelled.is_set():
            return  # caller timed out and went away; don't run a stale task
        try:
            res = task()
        except Exception as e:
            res = f"{e}\n{traceback.format_exc()}"
        try:
            response_queue.put_nowait(res)
        except queue.Full:  # pragma: no cover - single producer per queue
            pass

    _task_queue.put(_wrapped)
    try:
        return response_queue.get(timeout=timeout)
    except queue.Empty:
        cancelled.set()  # a not-yet-started task must not run after we give up
        return (
            f"GUI dispatch timed out after {timeout:.0f}s — FreeCAD's GUI "
            "thread is busy or blocked (e.g. by a modal dialog). The task was "
            "cancelled if it had not started."
        )
