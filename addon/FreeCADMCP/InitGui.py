import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore

class FreeCADMCPAddonWorkbench(Gui.Workbench):
    MenuText = "MCP Addon"
    ToolTip = "Addon for MCP Communication"

    def Initialize(self):
        from rpc_server import rpc_server

        commands = ["Start_RPC_Server", "Stop_RPC_Server"]
        self.appendToolbar("FreeCAD MCP", commands)
        self.appendMenu("FreeCAD MCP", commands)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(FreeCADMCPAddonWorkbench())


def auto_start_rpc_server():
    """Auto-start the RPC server after FreeCAD fully initializes."""
    try:
        from rpc_server.rpc_server import start_rpc_server, rpc_server_instance
        if rpc_server_instance is None:
            result = start_rpc_server()
            FreeCAD.Console.PrintMessage(f"[Auto-start] {result}\n")
    except Exception as e:
        FreeCAD.Console.PrintError(f"[Auto-start] Failed to start RPC server: {e}\n")


# Auto-start RPC server 2 seconds after FreeCAD loads
QtCore.QTimer.singleShot(2000, auto_start_rpc_server)
