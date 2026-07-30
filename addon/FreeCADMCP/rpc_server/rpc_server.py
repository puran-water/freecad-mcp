import FreeCAD
import FreeCADGui
import ObjectsFem

import contextlib
import base64
import io
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

from PySide import QtCore

from .gui_dispatch import dispatch_to_gui, drain_pending
from .parts_library import get_parts_list, insert_part_from_library
from .serialize import serialize_object

rpc_server_thread = None
rpc_server_instance = None
_stop_thread = None  # drains shutdown off the GUI thread; see stop_rpc_server

# Long-running work (TechDraw exports, imports, heavy recomputes) gets a
# larger budget than simple document edits.
EXECUTE_CODE_TIMEOUT = 300.0
SCREENSHOT_TIMEOUT = 120.0


def process_gui_tasks():
    drain_pending()
    QtCore.QTimer.singleShot(500, process_gui_tasks)


@dataclass
class Object:
    name: str
    type: str | None = None
    analysis: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


def set_object_property(
    doc: FreeCAD.Document, obj: FreeCAD.DocumentObject, properties: dict[str, Any]
):
    for prop, val in properties.items():
        try:
            if prop in obj.PropertiesList:
                if prop == "Placement" and isinstance(val, dict):
                    if "Base" in val:
                        pos = val["Base"]
                    elif "Position" in val:
                        pos = val["Position"]
                    else:
                        pos = {}
                    rot = val.get("Rotation", {})
                    placement = FreeCAD.Placement(
                        FreeCAD.Vector(
                            pos.get("x", 0),
                            pos.get("y", 0),
                            pos.get("z", 0),
                        ),
                        FreeCAD.Rotation(
                            FreeCAD.Vector(
                                rot.get("Axis", {}).get("x", 0),
                                rot.get("Axis", {}).get("y", 0),
                                rot.get("Axis", {}).get("z", 1),
                            ),
                            rot.get("Angle", 0),
                        ),
                    )
                    setattr(obj, prop, placement)

                elif isinstance(getattr(obj, prop), FreeCAD.Vector) and isinstance(
                    val, dict
                ):
                    vector = FreeCAD.Vector(
                        val.get("x", 0), val.get("y", 0), val.get("z", 0)
                    )
                    setattr(obj, prop, vector)

                elif prop in ["Base", "Tool", "Source", "Profile"] and isinstance(
                    val, str
                ):
                    ref_obj = doc.getObject(val)
                    if ref_obj:
                        setattr(obj, prop, ref_obj)
                    else:
                        raise ValueError(f"Referenced object '{val}' not found.")

                elif prop == "References" and isinstance(val, list):
                    refs = []
                    for ref_name, face in val:
                        ref_obj = doc.getObject(ref_name)
                        if ref_obj:
                            refs.append((ref_obj, face))
                        else:
                            raise ValueError(f"Referenced object '{ref_name}' not found.")
                    setattr(obj, prop, refs)

                else:
                    setattr(obj, prop, val)
            # ShapeColor is a property of the ViewObject
            elif prop == "ShapeColor" and isinstance(val, (list, tuple)):
                setattr(obj.ViewObject, prop, (float(val[0]), float(val[1]), float(val[2]), float(val[3])))

            elif prop == "ViewObject" and isinstance(val, dict):
                for k, v in val.items():
                    if k == "ShapeColor":
                        setattr(obj.ViewObject, k, (float(v[0]), float(v[1]), float(v[2]), float(v[3])))
                    else:
                        setattr(obj.ViewObject, k, v)

            else:
                setattr(obj, prop, val)

        except Exception as e:
            FreeCAD.Console.PrintError(f"Property '{prop}' assignment error: {e}\n")


def _ok(res) -> bool:
    return isinstance(res, dict) and res.get("ok") is True


def _error_of(res) -> str:
    if isinstance(res, dict):
        return str(res.get("error", res))
    return str(res)


class FreeCADRPC:
    """RPC server for FreeCAD"""

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        res = dispatch_to_gui(lambda: self._create_document_gui(name))
        if _ok(res):
            return {"success": True, "document_name": res["document_name"]}
        return {"success": False, "error": _error_of(res)}

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        obj = Object(
            name=obj_data.get("Name", "New_Object"),
            type=obj_data["Type"],
            analysis=obj_data.get("Analysis", None),
            properties=obj_data.get("Properties", {}),
        )
        res = dispatch_to_gui(lambda: self._create_object_gui(doc_name, obj))
        if _ok(res):
            return {"success": True, "object_name": res["object_name"]}
        return {"success": False, "error": _error_of(res)}

    def edit_object(self, doc_name: str, obj_name: str, properties: dict[str, Any]) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            properties=properties.get("Properties", {}),
        )
        res = dispatch_to_gui(lambda: self._edit_object_gui(doc_name, obj))
        if res is True:
            return {"success": True, "object_name": obj.name}
        return {"success": False, "error": _error_of(res)}

    def delete_object(self, doc_name: str, obj_name: str):
        res = dispatch_to_gui(lambda: self._delete_object_gui(doc_name, obj_name))
        if res is True:
            return {"success": True, "object_name": obj_name}
        return {"success": False, "error": _error_of(res)}

    def execute_code(self, code: str) -> dict[str, Any]:
        output_buffer = io.StringIO()
        def task():
            try:
                with contextlib.redirect_stdout(output_buffer):
                    exec(code, globals())
                FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
                return True
            except Exception as e:
                FreeCAD.Console.PrintError(
                    f"Error executing Python code: {e}\n"
                )
                return f"Error executing Python code: {e}\n"

        res = dispatch_to_gui(task, timeout=EXECUTE_CODE_TIMEOUT)
        if res is True:
            return {
                "success": True,
                "message": "Python code execution scheduled. \nOutput: " + output_buffer.getvalue()
            }
        return {"success": False, "error": _error_of(res)}

    def get_objects(self, doc_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return [serialize_object(obj) for obj in doc.Objects]
        else:
            return []

    def get_object(self, doc_name, obj_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return serialize_object(doc.getObject(obj_name))
        else:
            return None

    def insert_part_from_library(self, relative_path):
        res = dispatch_to_gui(lambda: self._insert_part_from_library(relative_path))
        if res is True:
            return {"success": True, "message": "Part inserted from library."}
        return {"success": False, "error": _error_of(res)}

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def get_parts_list(self):
        return get_parts_list()

    def get_active_screenshot(self, view_name: str = "Isometric") -> str:
        """Get a screenshot of the active view.

        Returns a base64-encoded string of the screenshot or None if a screenshot
        cannot be captured (e.g., when in TechDraw or Spreadsheet view).
        """
        # First check if the active view supports screenshots
        def check_view_supports_screenshots():
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view is None:
                    FreeCAD.Console.PrintWarning("No active view available\n")
                    return False

                view_type = type(active_view).__name__
                has_save_image = hasattr(active_view, 'saveImage')
                FreeCAD.Console.PrintMessage(f"View type: {view_type}, Has saveImage: {has_save_image}\n")
                return has_save_image
            except Exception as e:
                FreeCAD.Console.PrintError(f"Error checking view capabilities: {e}\n")
                return False

        supports_screenshots = dispatch_to_gui(check_view_supports_screenshots)

        # A timeout returns an error string (truthy) — only literal True means
        # the view can be captured.
        if supports_screenshots is not True:
            FreeCAD.Console.PrintWarning("Current view does not support screenshots\n")
            return None

        # If view supports screenshots, proceed with capture
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        res = dispatch_to_gui(
            lambda: self._save_active_screenshot(tmp_path, view_name),
            timeout=SCREENSHOT_TIMEOUT,
        )
        if res is True:
            try:
                with open(tmp_path, "rb") as image_file:
                    image_bytes = image_file.read()
                    encoded = base64.b64encode(image_bytes).decode("utf-8")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            return encoded
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            FreeCAD.Console.PrintWarning(f"Failed to capture screenshot: {res}\n")
            return None

    def _create_document_gui(self, name):
        # FreeCAD sanitises requested names ('My Doc' -> 'My_Doc'); report the
        # actual Document.Name or every follow-up call targets a nonexistent
        # document (upstream #91, 71b7db2).
        doc = FreeCAD.newDocument(name)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Document '{doc.Name}' created via RPC.\n")
        return {"ok": True, "document_name": doc.Name}

    def _create_object_gui(self, doc_name, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            try:
                if obj.type == "Fem::FemMeshGmsh" and obj.analysis:
                    from femmesh.gmshtools import GmshTools
                    created = getattr(doc, obj.analysis).addObject(ObjectsFem.makeMeshGmsh(doc, obj.name))[0]
                    if "Part" in obj.properties:
                        target_obj = doc.getObject(obj.properties["Part"])
                        if target_obj:
                            created.Part = target_obj
                        else:
                            raise ValueError(f"Referenced object '{obj.properties['Part']}' not found.")
                        del obj.properties["Part"]
                    else:
                        raise ValueError("'Part' property not found in properties.")

                    for param, value in obj.properties.items():
                        if hasattr(created, param):
                            setattr(created, param, value)
                    doc.recompute()

                    gmsh_tools = GmshTools(created)
                    gmsh_tools.create_mesh()
                    FreeCAD.Console.PrintMessage(
                        f"FEM Mesh '{created.Name}' generated successfully in '{doc_name}'.\n"
                    )
                elif obj.type.startswith("Fem::"):
                    fem_make_methods = {
                        "MaterialCommon": ObjectsFem.makeMaterialSolid,
                        "AnalysisPython": ObjectsFem.makeAnalysis,
                    }
                    obj_type_short = obj.type.split("::")[1]
                    method_name = "make" + obj_type_short
                    make_method = fem_make_methods.get(obj_type_short, getattr(ObjectsFem, method_name, None))

                    if callable(make_method):
                        created = make_method(doc, obj.name)
                        set_object_property(doc, created, obj.properties)
                        FreeCAD.Console.PrintMessage(
                            f"FEM object '{created.Name}' created with '{method_name}'.\n"
                        )
                    else:
                        raise ValueError(f"No creation method '{method_name}' found in ObjectsFem.")
                    if obj.type != "Fem::AnalysisPython" and obj.analysis:
                        getattr(doc, obj.analysis).addObject(created)
                else:
                    created = doc.addObject(obj.type, obj.name)
                    set_object_property(doc, created, obj.properties)
                    FreeCAD.Console.PrintMessage(
                        f"{created.TypeId} '{created.Name}' added to '{doc_name}' via RPC.\n"
                    )

                doc.recompute()
                # FreeCAD de-duplicates requested names ('Box' -> 'Box001');
                # report the actual DocumentObject.Name (upstream #91).
                return {"ok": True, "object_name": created.Name}
            except Exception as e:
                return str(e)
        else:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

    def _edit_object_gui(self, doc_name: str, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(f"Object '{obj.name}' not found in document '{doc_name}'.\n")
            return f"Object '{obj.name}' not found in document '{doc_name}'.\n"

        try:
            # For Fem::ConstraintFixed
            if hasattr(obj_ins, "References") and "References" in obj.properties:
                refs = []
                for ref_name, face in obj.properties["References"]:
                    ref_obj = doc.getObject(ref_name)
                    if ref_obj:
                        refs.append((ref_obj, face))
                    else:
                        raise ValueError(f"Referenced object '{ref_name}' not found.")
                obj_ins.References = refs
                FreeCAD.Console.PrintMessage(
                    f"References updated for '{obj.name}' in '{doc_name}'.\n"
                )
                # delete References from properties
                del obj.properties["References"]
            set_object_property(doc, obj_ins, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _delete_object_gui(self, doc_name: str, obj_name: str):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        try:
            doc.removeObject(obj_name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj_name}' deleted via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _insert_part_from_library(self, relative_path):
        try:
            insert_part_from_library(relative_path)
            return True
        except Exception as e:
            return str(e)

    def _save_active_screenshot(self, save_path: str, view_name: str = "Isometric"):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            # Check if the view supports screenshots
            if not hasattr(view, 'saveImage'):
                return "Current view does not support screenshots"

            if view_name == "Isometric":
                view.viewIsometric()
            elif view_name == "Front":
                view.viewFront()
            elif view_name == "Top":
                view.viewTop()
            elif view_name == "Right":
                view.viewRight()
            elif view_name == "Back":
                view.viewBack()
            elif view_name == "Left":
                view.viewLeft()
            elif view_name == "Bottom":
                view.viewBottom()
            elif view_name == "Dimetric":
                view.viewDimetric()
            elif view_name == "Trimetric":
                view.viewTrimetric()
            else:
                raise ValueError(f"Invalid view name: {view_name}")
            view.fitAll()
            view.saveImage(save_path, 1)
            return True
        except Exception as e:
            return str(e)


def start_rpc_server(host="0.0.0.0", port=9875):
    """Start the XML-RPC server.

    Args:
        host: Bind address. Default "0.0.0.0" accepts connections from WSL and
              the network — required when WSL runs in NAT mode (the MCP server
              connects to the Windows host IP, not loopback). Under WSL
              mirrored networking, "127.0.0.1" suffices and is the safer bind;
              pass host="127.0.0.1" or set the FREECAD_MCP_BIND environment
              variable. Non-loopback binds log a security warning.
        port: Port number (default 9875)
    """
    global rpc_server_thread, rpc_server_instance, _stop_thread

    if rpc_server_instance:
        return "RPC Server already running."

    # A previous stop may still be draining an in-flight request off-thread;
    # binding before its server_close() would hit the old socket (upstream
    # #90, d85fd5d).
    if _stop_thread is not None and _stop_thread.is_alive():
        _stop_thread.join(timeout=5.0)
        if _stop_thread.is_alive():
            return ("RPC Server is still stopping (a request is draining); "
                    "try again in a few seconds.")

    host = os.environ.get("FREECAD_MCP_BIND", host)

    rpc_server_instance = SimpleXMLRPCServer(
        (host, port), allow_none=True, logRequests=False
    )
    rpc_server_instance.register_instance(FreeCADRPC())

    def server_loop():
        FreeCAD.Console.PrintMessage(f"RPC Server started at {host}:{port}\n")
        if host not in ("127.0.0.1", "localhost", "::1"):
            FreeCAD.Console.PrintWarning(
                "  RPC server is bound to a non-loopback address and accepts "
                "unauthenticated connections from the network. Use "
                "FREECAD_MCP_BIND=127.0.0.1 unless WSL NAT mode requires "
                "otherwise.\n"
            )
        rpc_server_instance.serve_forever()

    rpc_server_thread = threading.Thread(target=server_loop, daemon=True)
    rpc_server_thread.start()

    QtCore.QTimer.singleShot(500, process_gui_tasks)

    return f"RPC Server started at {host}:{port}."


def stop_rpc_server():
    global rpc_server_instance, rpc_server_thread, _stop_thread

    if not rpc_server_instance:
        return "RPC Server was not running."

    server = rpc_server_instance
    thread = rpc_server_thread
    rpc_server_instance = None
    rpc_server_thread = None

    def _shutdown_and_close():
        # shutdown() blocks until serve_forever drains the in-flight request,
        # and that request may itself be waiting on dispatch_to_gui — running
        # this on the GUI thread (menu command) froze the UI for up to the
        # dispatch timeout. server_close() must always follow: without it the
        # listening socket stays bound and Stop -> Start behavior is
        # undefined (upstream #90, d85fd5d).
        try:
            server.shutdown()
            if thread is not None:
                thread.join(timeout=10.0)
                if thread.is_alive():
                    FreeCAD.Console.PrintWarning(
                        "MCP RPC: server thread still draining a request; "
                        "socket closes when it finishes.\n"
                    )
        finally:
            server.server_close()
        FreeCAD.Console.PrintMessage("RPC Server stopped.\n")

    _stop_thread = threading.Thread(target=_shutdown_and_close, daemon=True)
    _stop_thread.start()
    return "RPC Server stopping…"


class StartRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Start RPC Server", "ToolTip": "Start RPC Server"}

    def Activated(self):
        msg = start_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class StopRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Stop RPC Server", "ToolTip": "Stop RPC Server"}

    def Activated(self):
        msg = stop_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


FreeCADGui.addCommand("Start_RPC_Server", StartRPCServerCommand())
FreeCADGui.addCommand("Stop_RPC_Server", StopRPCServerCommand())
