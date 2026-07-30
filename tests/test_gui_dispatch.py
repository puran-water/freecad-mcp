"""Smoke tests for the #87 port (upstream 2cd4fd8): per-call dispatch with
timeout + cancellation."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from rpc_server import gui_dispatch


def test_timeout_cancels_queued_task():
    """A task still queued when its caller times out must never run."""
    ran = threading.Event()

    result = gui_dispatch.dispatch_to_gui(ran.set, timeout=0.05)

    assert isinstance(result, str) and "timed out" in result
    # The GUI heartbeat fires later — the cancelled task must refuse to run.
    gui_dispatch.drain_pending()
    assert not ran.is_set()


def test_late_result_does_not_poison_next_call():
    """Legacy shared response queue let call N's late answer become call N+1's
    result; per-call queues must isolate them."""
    result_1 = gui_dispatch.dispatch_to_gui(lambda: "stale-answer", timeout=0.05)
    assert isinstance(result_1, str) and "timed out" in result_1

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(gui_dispatch.dispatch_to_gui, lambda: "fresh-answer", 5.0)
        deadline = time.monotonic() + 5.0
        while not fut.done() and time.monotonic() < deadline:
            gui_dispatch.drain_pending()  # drains cancelled task 1 + task 2
            time.sleep(0.01)
        assert fut.result(timeout=1.0) == "fresh-answer"


def test_task_exception_returns_error_string():
    def boom():
        raise ValueError("kaput")

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(gui_dispatch.dispatch_to_gui, boom, 5.0)
        deadline = time.monotonic() + 5.0
        while not fut.done() and time.monotonic() < deadline:
            gui_dispatch.drain_pending()
            time.sleep(0.01)
        result = fut.result(timeout=1.0)

    assert isinstance(result, str) and "kaput" in result
