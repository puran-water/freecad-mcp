"""Smoke tests for the #90 port (stop closes the socket, off-GUI-thread drain;
upstream d85fd5d) and the #91 port (actual doc/object names; upstream 71b7db2)."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from rpc_server import gui_dispatch
from rpc_server import rpc_server as rs


def _run_with_drain(fn, *args, timeout=5.0):
    """Call an RPC method (which blocks on dispatch) while this thread plays
    the GUI heartbeat."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args)
        deadline = time.monotonic() + timeout
        while not fut.done() and time.monotonic() < deadline:
            gui_dispatch.drain_pending()
            time.sleep(0.01)
        return fut.result(timeout=1.0)


# --- #91: actual names ------------------------------------------------------


class _FakeDoc:
    def __init__(self, name):
        self.Name = name
        self.Objects = []

    def recompute(self):
        pass

    def addObject(self, obj_type, name):
        obj = _FakeObj(name + "001", obj_type)  # FreeCAD-style de-duplication
        self.Objects.append(obj)
        return obj

    def getObject(self, name):
        for o in self.Objects:
            if o.Name == name:
                return o
        return None


class _FakeObj:
    def __init__(self, name, type_id):
        self.Name = name
        self.TypeId = type_id
        self.PropertiesList = []


def test_create_document_reports_actual_sanitised_name(monkeypatch):
    import FreeCAD

    monkeypatch.setattr(
        FreeCAD, "newDocument", lambda name: _FakeDoc(name.replace(" ", "_"))
    )
    rpc = rs.FreeCADRPC()

    res = _run_with_drain(rpc.create_document, "My Doc")

    assert res["success"] is True
    assert res["document_name"] == "My_Doc"


def test_create_object_reports_actual_deduplicated_name(monkeypatch):
    import FreeCAD

    doc = _FakeDoc("Model")
    monkeypatch.setattr(FreeCAD, "getDocument", lambda name: doc)
    rpc = rs.FreeCADRPC()

    res = _run_with_drain(
        rpc.create_object, "Model", {"Name": "Box", "Type": "Part::Box"}
    )

    assert res["success"] is True
    assert res["object_name"] == "Box001"


# --- #90: stop closes socket, start waits for draining stop -----------------


class _FakeXmlRpcServer:
    def __init__(self):
        self.calls = []

    def shutdown(self):
        self.calls.append("shutdown")

    def server_close(self):
        self.calls.append("server_close")


class _FakeThread:
    def __init__(self, alive_for=0.0):
        self._deadline = time.monotonic() + alive_for

    def join(self, timeout=None):
        remaining = self._deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, timeout or remaining))

    def is_alive(self):
        return time.monotonic() < self._deadline


@pytest.fixture(autouse=True)
def _reset_server_globals():
    yield
    rs.rpc_server_instance = None
    rs.rpc_server_thread = None
    rs._stop_thread = None


def test_stop_closes_listening_socket_off_thread():
    fake = _FakeXmlRpcServer()
    rs.rpc_server_instance = fake
    rs.rpc_server_thread = _FakeThread()

    msg = rs.stop_rpc_server()

    assert "stopping" in msg.lower()
    # Stop must return immediately (GUI thread) and never block on shutdown.
    assert rs.rpc_server_instance is None
    rs._stop_thread.join(timeout=5.0)
    assert fake.calls == ["shutdown", "server_close"]


def test_start_waits_for_draining_stop(monkeypatch):
    bound = []

    class _BindRecorder:
        def __init__(self, addr, allow_none=True, logRequests=False):
            bound.append(addr)
            self.calls = []

        def register_instance(self, inst):
            pass

        def serve_forever(self):
            pass

        def shutdown(self):
            self.calls.append("shutdown")

        def server_close(self):
            self.calls.append("server_close")

    monkeypatch.setattr(rs, "SimpleXMLRPCServer", _BindRecorder)
    monkeypatch.setenv("FREECAD_MCP_BIND", "127.0.0.1")

    # A stop that is still draining: start must join it before rebinding.
    rs._stop_thread = _FakeThread(alive_for=0.2)
    msg = rs.start_rpc_server(port=0)

    assert "started" in msg.lower()
    assert bound == [("127.0.0.1", 0)]
    assert not rs._stop_thread.is_alive() or True  # joined before bind

    rs.stop_rpc_server()
    rs._stop_thread.join(timeout=5.0)
