"""Headless test harness for the FreeCAD MCP addon.

The addon imports FreeCAD/FreeCADGui/ObjectsFem/PySide at module level; none
exist outside a running FreeCAD GUI. Stub them into sys.modules BEFORE the
addon package is imported so the dispatch/lifecycle/naming logic is testable
headlessly.
"""

import sys
import types
from pathlib import Path

ADDON_PKG_DIR = Path(__file__).resolve().parents[1] / "addon" / "FreeCADMCP"


class _Console:
    @staticmethod
    def PrintMessage(msg):
        pass

    @staticmethod
    def PrintWarning(msg):
        pass

    @staticmethod
    def PrintError(msg):
        pass


def _install_stubs() -> None:
    if "FreeCAD" in sys.modules:
        return

    fc = types.ModuleType("FreeCAD")
    fc.Console = _Console
    fc.Document = object
    fc.DocumentObject = object
    fc.Vector = lambda *a, **k: ("Vector", a)
    fc.Rotation = lambda *a, **k: ("Rotation", a)
    fc.Placement = lambda *a, **k: ("Placement", a)
    fc.newDocument = lambda name="": None  # tests monkeypatch
    fc.getDocument = lambda name: None  # tests monkeypatch
    fc.listDocuments = lambda: {}
    sys.modules["FreeCAD"] = fc

    gui = types.ModuleType("FreeCADGui")
    gui.addCommand = lambda *a, **k: None
    gui.ActiveDocument = None
    sys.modules["FreeCADGui"] = gui

    sys.modules["ObjectsFem"] = types.ModuleType("ObjectsFem")

    pyside = types.ModuleType("PySide")
    qtcore = types.ModuleType("PySide.QtCore")

    class QTimer:
        @staticmethod
        def singleShot(ms, fn):
            pass  # no Qt event loop in tests; tests drain manually

    qtcore.QTimer = QTimer
    pyside.QtCore = qtcore
    sys.modules["PySide"] = pyside
    sys.modules["PySide.QtCore"] = qtcore


_install_stubs()

if str(ADDON_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(ADDON_PKG_DIR))
