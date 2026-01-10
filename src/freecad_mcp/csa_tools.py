"""
CSA (Control System Architecture) Tools for FreeCAD MCP

Extends freecad-mcp with tools for CSA diagram generation:
- import_csa_topology: Import YAML topology and create CSA objects
- export_csa_topology: Export CSA diagram to YAML/JSON
- add_csa_controller: Add a PLC/DCS/PAC controller
- add_csa_device: Add a device (RIO, HMI, SCADA, etc.)
- add_csa_link: Add a network link between components
- create_csa_techdraw_sheet: Generate TechDraw PDF output
- run_csa_layout: Run layout algorithm on CSA diagram

These tools use the stable API pattern with JSON payloads for
communication with the CSAWorkbench addon.
"""

import json
from typing import Any, Literal

import structlog
from mcp.server.fastmcp import Context
from mcp.types import TextContent, ImageContent

from .path_utils import wsl_to_windows_path
from .response_filters import DetailLevel

logger = structlog.get_logger("FreeCADMCPserver.csa")


def _extract_json_from_output(output: str) -> dict | None:
    """Extract a JSON object from FreeCAD command output.

    FreeCAD's execute_code returns output that may have prefix text.
    This function finds the first complete JSON object in the output.

    Args:
        output: Raw output string from FreeCAD

    Returns:
        Parsed JSON dict, or None if no valid JSON found
    """
    if not output:
        return None

    first_brace = output.find("{")
    if first_brace < 0:
        return None

    candidate = output[first_brace:]

    # Count braces to find the complete JSON object
    depth = 0
    in_string = False
    escape_next = False
    json_end = -1

    for i, char in enumerate(candidate):
        if escape_next:
            escape_next = False
            continue

        if char == '\\' and in_string:
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break

    if json_end > 0:
        json_str = candidate[:json_end]
        return json.loads(json_str)

    return None


def register_csa_tools(mcp, get_freecad_connection, add_screenshot_if_available):
    """Register CSA tools with the MCP server.

    Args:
        mcp: The FastMCP server instance
        get_freecad_connection: Function to get FreeCAD connection
        add_screenshot_if_available: Helper function for adding screenshots
    """

    @mcp.tool()
    def import_csa_topology(
        ctx: Context,
        doc_name: str,
        topology_yaml: str,
        layout_algorithm: Literal["networkx_spring", "networkx_hierarchical", "elk_hierarchical", "simple_grid"] = "networkx_spring",
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Import a CSA topology from YAML and create FreeCAD objects.

        Creates CSAProject, CSAController, CSADevice, and CSALink objects
        from the provided YAML topology definition. Optionally runs a layout
        algorithm to position components.

        Args:
            doc_name: Name of the FreeCAD document (will be created if needed)
            topology_yaml: YAML string defining the CSA topology
            layout_algorithm: Layout algorithm to use:
                - "networkx_spring": Force-directed layout (default)
                - "networkx_hierarchical": Purdue-level based hierarchical
                - "elk_hierarchical": ELK layered layout (requires Node.js)
                - "simple_grid": Simple grid fallback
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message with project details or error message

        Examples:
            ```yaml
            metadata:
              project_name: "WWTP Control System"
              revision: "A"
            zones:
              - id: level_1
                purdue_level: 1
            controllers:
              - id: PLC-101
                type: PLC
                zone: level_1
                equipment_tags: ["200-MB-001", "200-BL-001"]
            devices:
              - id: RIO-101
                type: RemoteIO
                parent_controller: PLC-101
                zone: level_0
            links:
              - source: PLC-101
                target: RIO-101
                protocol: Ethernet_IP
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Escape the YAML for embedding in Python code
            yaml_escaped = topology_yaml.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')

            code = f'''
import json

# Try CSAWorkbench stable API first
try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "import_topology",
        "doc_name": "{doc_name}",
        "topology_yaml": '{yaml_escaped}',
        "layout_algorithm": "{layout_algorithm}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    # CSAWorkbench not installed - return error
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"CSA topology imported successfully to '{doc_name}'"
                    if "project_name" in result:
                        msg += f"\nProject: {result['project_name']}"
                    if "controller_count" in result:
                        msg += f"\nControllers: {result['controller_count']}"
                    if "device_count" in result:
                        msg += f"\nDevices: {result['device_count']}"
                    if "link_count" in result:
                        msg += f"\nLinks: {result['link_count']}"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error during import")
                    response = [TextContent(type="text", text=f"Failed to import CSA topology: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute import code")
            return [TextContent(type="text", text=f"Failed to import CSA topology: {error_msg}")]

        except Exception as e:
            logger.error("import_csa_topology_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to import CSA topology: {str(e)}")]

    @mcp.tool()
    def export_csa_topology(
        ctx: Context,
        doc_name: str,
        format: Literal["yaml", "json"] = "yaml",
        output_path: str | None = None,
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Export CSA topology from FreeCAD document.

        Extracts CSAProject and all child objects (controllers, devices, links)
        and exports them as YAML or JSON.

        Args:
            doc_name: Name of the FreeCAD document
            format: Output format ("yaml" or "json")
            output_path: File path to write (optional, returns content if not provided)
            include_screenshot: Whether to include a screenshot

        Returns:
            Topology content or success message with file path
        """
        freecad = get_freecad_connection()

        try:
            # Convert WSL path if needed
            output_path_win = wsl_to_windows_path(output_path) if output_path else ""

            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "export_topology",
        "doc_name": "{doc_name}",
        "format": "{format}",
        "output_path": "{output_path_win}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    if result.get("exported"):
                        # File was written to disk
                        msg = f"CSA topology exported to: {result.get('output_path', output_path)}"
                        response = [TextContent(type="text", text=msg)]
                    else:
                        # Return in-memory content
                        content = result.get("content", "")
                        response = [TextContent(type="text", text=content)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error during export")
                    response = [TextContent(type="text", text=f"Failed to export CSA topology: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute export code")
            return [TextContent(type="text", text=f"Failed to export CSA topology: {error_msg}")]

        except Exception as e:
            logger.error("export_csa_topology_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to export CSA topology: {str(e)}")]

    @mcp.tool()
    def add_csa_controller(
        ctx: Context,
        doc_name: str,
        controller_id: str,
        controller_type: Literal["PLC", "DCS", "PAC", "Safety_PLC", "Soft_PLC", "Edge_Controller", "Motion_Controller", "Redundant_PLC"] = "PLC",
        zone: str = "",
        equipment_tags: list[str] | None = None,
        manufacturer: str = "",
        model: str = "",
        ip_address: str = "",
        description: str = "",
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Add a controller (PLC/DCS/PAC) to the CSA diagram.

        Creates a CSAController object in the document's CSAProject.
        The controller can be linked to equipment tags from control-philosophy-skill.

        Args:
            doc_name: Name of the FreeCAD document
            controller_id: Unique controller identifier (e.g., "PLC-101")
            controller_type: Controller type (PLC, DCS, PAC, etc.)
            zone: Network zone ID (e.g., "level_1" for Purdue Level 1)
            equipment_tags: List of equipment tags this controller manages
            manufacturer: Equipment manufacturer
            model: Equipment model number
            ip_address: Network IP address
            description: Controller description
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message or error

        Examples:
            Add a main PLC:
            ```json
            {
                "doc_name": "WWTP_CSA",
                "controller_id": "PLC-101",
                "controller_type": "PLC",
                "zone": "level_1",
                "equipment_tags": ["200-MB-001", "200-BL-001"],
                "manufacturer": "Siemens",
                "model": "S7-1500"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            tags_json = json.dumps(equipment_tags or [])

            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "add_controller",
        "doc_name": "{doc_name}",
        "controller_id": "{controller_id}",
        "controller_type": "{controller_type}",
        "zone": "{zone}",
        "equipment_tags": {tags_json},
        "manufacturer": "{manufacturer}",
        "model": "{model}",
        "ip_address": "{ip_address}",
        "description": "{description}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"Controller '{controller_id}' ({controller_type}) added successfully"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error")
                    response = [TextContent(type="text", text=f"Failed to add controller: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute add controller code")
            return [TextContent(type="text", text=f"Failed to add controller: {error_msg}")]

        except Exception as e:
            logger.error("add_csa_controller_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to add controller: {str(e)}")]

    @mcp.tool()
    def add_csa_device(
        ctx: Context,
        doc_name: str,
        device_id: str,
        device_type: Literal["RemoteIO", "HMI", "SCADA", "Historian", "OPC_UA_Server", "Gateway", "VFD", "Soft_Starter", "MCC", "Industrial_PC", "Switch", "Router", "Firewall", "Wireless_AP", "Junction_Box", "Marshalling_Cabinet"] = "RemoteIO",
        parent_controller: str = "",
        zone: str = "",
        model: str = "",
        ip_address: str = "",
        description: str = "",
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Add a device to the CSA diagram.

        Creates a CSADevice object in the document's CSAProject.
        Devices are typically children of controllers (e.g., Remote I/O modules).

        Args:
            doc_name: Name of the FreeCAD document
            device_id: Unique device identifier (e.g., "RIO-101", "HMI-001")
            device_type: Device type (RemoteIO, HMI, SCADA, etc.)
            parent_controller: ID of the parent controller
            zone: Network zone ID
            model: Equipment model
            ip_address: Network IP address
            description: Device description
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message or error

        Examples:
            Add a Remote I/O module:
            ```json
            {
                "doc_name": "WWTP_CSA",
                "device_id": "RIO-101",
                "device_type": "RemoteIO",
                "parent_controller": "PLC-101",
                "zone": "level_0"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "add_device",
        "doc_name": "{doc_name}",
        "device_id": "{device_id}",
        "device_type": "{device_type}",
        "parent_controller": "{parent_controller}",
        "zone": "{zone}",
        "model": "{model}",
        "ip_address": "{ip_address}",
        "description": "{description}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"Device '{device_id}' ({device_type}) added successfully"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error")
                    response = [TextContent(type="text", text=f"Failed to add device: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute add device code")
            return [TextContent(type="text", text=f"Failed to add device: {error_msg}")]

        except Exception as e:
            logger.error("add_csa_device_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to add device: {str(e)}")]

    @mcp.tool()
    def add_csa_link(
        ctx: Context,
        doc_name: str,
        source: str,
        target: str,
        protocol: Literal["Ethernet_IP", "Profinet", "Modbus_TCP", "Modbus_RTU", "Profibus", "DeviceNet", "ControlNet", "HART", "Foundation_Fieldbus", "OPC_UA", "MQTT", "BACnet"] = "Ethernet_IP",
        network: str = "",
        cable_type: str = "",
        source_port: str = "",
        target_port: str = "",
        description: str = "",
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Add a network link between CSA components.

        Creates a CSALink object connecting a source to a target.
        Links can specify the protocol, cable type, and port connections.

        Args:
            doc_name: Name of the FreeCAD document
            source: Source controller/device ID
            target: Target controller/device ID
            protocol: Network protocol (Ethernet_IP, Profinet, Modbus_TCP, etc.)
            network: Network ID this link belongs to
            cable_type: Physical cable specification
            source_port: Port ID on source device
            target_port: Port ID on target device
            description: Link description
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message or error

        Examples:
            Connect PLC to Remote I/O:
            ```json
            {
                "doc_name": "WWTP_CSA",
                "source": "PLC-101",
                "target": "RIO-101",
                "protocol": "Ethernet_IP",
                "cable_type": "Cat6 STP"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "add_link",
        "doc_name": "{doc_name}",
        "source": "{source}",
        "target": "{target}",
        "protocol": "{protocol}",
        "network": "{network}",
        "cable_type": "{cable_type}",
        "source_port": "{source_port}",
        "target_port": "{target_port}",
        "description": "{description}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"Link '{source}' -> '{target}' ({protocol}) added successfully"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error")
                    response = [TextContent(type="text", text=f"Failed to add link: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute add link code")
            return [TextContent(type="text", text=f"Failed to add link: {error_msg}")]

        except Exception as e:
            logger.error("add_csa_link_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to add link: {str(e)}")]

    @mcp.tool()
    def run_csa_layout(
        ctx: Context,
        doc_name: str,
        algorithm: Literal["networkx_spring", "networkx_hierarchical", "elk_hierarchical", "simple_grid"] = "networkx_spring",
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Run layout algorithm on CSA diagram.

        Positions controllers and devices according to the selected algorithm.
        Hierarchical layouts respect Purdue level zones.

        Args:
            doc_name: Name of the FreeCAD document
            algorithm: Layout algorithm:
                - "networkx_spring": Force-directed spring layout
                - "networkx_hierarchical": Purdue-level based layout
                - "elk_hierarchical": ELK layered layout (requires Node.js + elkjs)
                - "simple_grid": Simple grid fallback
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message with layout statistics or error
        """
        freecad = get_freecad_connection()

        try:
            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "run_layout",
        "doc_name": "{doc_name}",
        "algorithm": "{algorithm}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"Layout completed using '{algorithm}'"
                    if "node_count" in result:
                        msg += f"\nNodes positioned: {result['node_count']}"
                    if "edge_count" in result:
                        msg += f"\nEdges routed: {result['edge_count']}"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error during layout")
                    response = [TextContent(type="text", text=f"Failed to run layout: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute layout code")
            return [TextContent(type="text", text=f"Failed to run layout: {error_msg}")]

        except Exception as e:
            logger.error("run_csa_layout_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to run layout: {str(e)}")]

    @mcp.tool()
    def create_csa_techdraw_sheet(
        ctx: Context,
        doc_name: str,
        title: str = "Control System Architecture",
        sheet_number: str = "CSA-001",
        template: str = "A1_Landscape_CSA",
        revision: str = "A",
        export_pdf_path: str | None = None,
        include_screenshot: bool = False,
    ) -> list[TextContent | ImageContent]:
        """Create a TechDraw sheet for CSA diagram.

        Generates a TechDraw page with CSA symbols and annotations.
        Can optionally export directly to PDF.

        Args:
            doc_name: Name of the FreeCAD document
            title: Sheet title
            sheet_number: Sheet number for title block (e.g., "CSA-001")
            template: Template name (default: A1_Landscape_CSA)
            revision: Document revision letter (A, B, C, etc.)
            export_pdf_path: Path to export PDF (optional)
            include_screenshot: Whether to include a screenshot

        Returns:
            Success message with sheet details or error

        Examples:
            Create and export CSA drawing:
            ```json
            {
                "doc_name": "WWTP_CSA",
                "title": "WWTP Control System Architecture",
                "sheet_number": "CSA-101",
                "template": "A1_Landscape_CSA",
                "revision": "B",
                "export_pdf_path": "/output/WWTP_CSA_RevB.pdf"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Convert WSL path if needed
            pdf_path_win = wsl_to_windows_path(export_pdf_path) if export_pdf_path else ""

            code = f'''
import json

try:
    from CSAWorkbench.api import execute_action
    payload = {{
        "action": "create_techdraw_sheet",
        "doc_name": "{doc_name}",
        "title": "{title}",
        "sheet_number": "{sheet_number}",
        "template": "{template}",
        "revision": "{revision}",
        "export_pdf_path": "{pdf_path_win}"
    }}
    result = execute_action(payload)
    print(json.dumps(result))
except ImportError:
    print(json.dumps({{"success": False, "error": "CSAWorkbench addon not installed"}}))
except Exception as e:
    import traceback
    print(json.dumps({{"success": False, "error": str(e), "traceback": traceback.format_exc()}}))
'''

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot() if include_screenshot else None

            if res.get("success"):
                output = res.get("message", "") or res.get("output", "")
                result = _extract_json_from_output(output)

                if result and result.get("success"):
                    msg = f"TechDraw sheet '{title}' created successfully"
                    if result.get("page_name"):
                        msg += f"\nPage: {result['page_name']}"
                    if result.get("sheet_number"):
                        msg += f"\nSheet number: {result['sheet_number']}"
                    if result.get("pdf_exported") and result.get("pdf_path"):
                        msg += f"\nPDF exported to: {result['pdf_path']}"
                    response = [TextContent(type="text", text=msg)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                elif result:
                    error_msg = result.get("error", "Unknown error creating sheet")
                    response = [TextContent(type="text", text=f"Failed to create TechDraw sheet: {error_msg}")]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)

            error_msg = res.get("error", "Failed to execute TechDraw code")
            return [TextContent(type="text", text=f"Failed to create TechDraw sheet: {error_msg}")]

        except Exception as e:
            logger.error("create_csa_techdraw_sheet_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to create TechDraw sheet: {str(e)}")]
