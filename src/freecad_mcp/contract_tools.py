"""
Process Engineering Contract Tools for FreeCAD MCP

Extends freecad-mcp with tools for Spatial Contract interchange:
- export_contract_json: Export site boundary, equipment envelopes to contract JSON
- apply_placements: Apply solved positions from site-fit back to FreeCAD
- export_glb: Export mesh in GLB format for Blender visualization

These tools implement the bridge between FreeCAD (engineering truth) and
site-fit (constraint solver) / Blender (visualization) layers.
"""

import json
import hashlib
import logging
import os
import tempfile
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import Context
from mcp.types import TextContent, ImageContent

from .path_utils import wsl_to_windows_path
from .response_filters import DetailLevel, filter_contract_response

logger = logging.getLogger("FreeCADMCPserver.contract")

# Unit conversion: FreeCAD uses mm internally, contract uses meters
MM_TO_M = 0.001
M_TO_MM = 1000.0


def get_rect_dims_at_rotation(w: float, h: float, rotation_deg: int) -> tuple[float, float]:
    """Get rectangle dimensions adjusted for rotation.

    Site-fit solver uses rotation to swap the axis-aligned bounding box.
    For rotation 90 or 270, width and height are swapped.

    Args:
        w: Original width (X-dimension at rotation=0)
        h: Original height (Y-dimension at rotation=0)
        rotation_deg: Rotation in degrees (0, 90, 180, 270)

    Returns:
        Tuple of (effective_width, effective_height) for FreeCAD Part::Box
    """
    if rotation_deg in (90, 270):
        return h, w
    return w, h


def _extract_json_from_output(output: str) -> dict | None:
    """Extract a JSON object from FreeCAD command output.

    FreeCAD's execute_code returns output that may have prefix text like:
    "Python code execution scheduled. \nOutput: {...json...}"

    This function finds the first complete JSON object in the output.

    Args:
        output: Raw output string from FreeCAD

    Returns:
        Parsed JSON dict, or None if no valid JSON found
    """
    if not output:
        return None

    # Find the first '{' which should start our JSON
    first_brace = output.find("{")
    if first_brace < 0:
        return None

    # Try to parse from this position - json.loads will stop at the end of valid JSON
    # We need to find where the JSON ends by matching braces
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


def register_contract_tools(mcp, get_freecad_connection, add_screenshot_if_available):
    """Register contract tools with the MCP server.

    Args:
        mcp: The FastMCP server instance
        get_freecad_connection: Function to get FreeCAD connection
        add_screenshot_if_available: Helper function for adding screenshots
    """

    @mcp.tool()
    def export_contract_json(
        ctx: Context,
        doc_name: str,
        project_name: str,
        boundary_object: str | None = None,
        equipment_prefix: str = "",
        output_path: str | None = None,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Export a Spatial Contract JSON from the FreeCAD document.

        Exports site boundary, equipment envelopes, and parameters for use with
        site-fit constraint solver. All dimensions are converted to meters.

        Args:
            doc_name: Name of the FreeCAD document
            project_name: Project identifier for the contract
            boundary_object: Name of Draft Wire/Polyline representing site boundary (optional)
            equipment_prefix: Only export objects with names starting with this prefix (e.g., "TK-", "P-")
            output_path: File path to write JSON (optional, returns JSON in response if not provided)

        Returns:
            Contract JSON content or success message with file path

        Examples:
            Export all equipment from document:
            ```json
            {
                "doc_name": "WWT_Plant",
                "project_name": "danone-india-etp",
                "boundary_object": "SiteBoundary"
            }
            ```

            Export only tanks:
            ```json
            {
                "doc_name": "WWT_Plant",
                "project_name": "danone-india-etp",
                "equipment_prefix": "TK-"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Build the extraction code to run in FreeCAD
            extract_code = f'''
import FreeCAD
import json
import math

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    raise ValueError("Document '{doc_name}' not found")

result = {{
    "project": {{
        "name": "{project_name}",
        "crs": "local",
        "origin": {{"easting": 0, "northing": 0, "elevation": 0}},
        "rotation_deg": 0,
        "unit": "m",
        "version": "1.0.0"
    }},
    "site": {{
        "boundary": [],
        "keepouts": [],
        "entrances": []
    }},
    "equipment": [],
    "placements": [],
    "connections": [],
    "viz_overrides": [],
    "metadata": {{
        "created_at": "{datetime.utcnow().isoformat()}Z",
        "created_by": "freecad-mcp/export_contract_json",
        "source_file": doc.FileName if doc.FileName else doc.Name
    }}
}}

# Unit conversion factor (mm to m)
MM_TO_M = 0.001

# Extract site boundary if specified
boundary_name = "{boundary_object}" if "{boundary_object}" else None
if boundary_name:
    boundary_obj = doc.getObject(boundary_name)
    if boundary_obj and hasattr(boundary_obj, "Points"):
        # Draft Wire/Polyline has Points property
        result["site"]["boundary"] = [
            [p.x * MM_TO_M, p.y * MM_TO_M] for p in boundary_obj.Points
        ]
    elif boundary_obj and hasattr(boundary_obj, "Shape"):
        # Extract from shape vertices
        verts = boundary_obj.Shape.Vertexes
        result["site"]["boundary"] = [
            [v.X * MM_TO_M, v.Y * MM_TO_M] for v in verts
        ]

# Equipment type mapping based on object type and name patterns
def infer_equipment_type(obj_name, obj_type):
    name_lower = obj_name.lower()
    if "tank" in name_lower or name_lower.startswith("tk"):
        return "storage_tank"
    elif "reactor" in name_lower or name_lower.startswith("r-"):
        return "reactor"
    elif "pump" in name_lower or name_lower.startswith("p-"):
        return "pump"
    elif "clarifier" in name_lower:
        return "clarifier"
    elif "thickener" in name_lower:
        return "thickener"
    elif "filter" in name_lower:
        return "filter"
    elif "blower" in name_lower or name_lower.startswith("bl-"):
        return "blower"
    elif "compressor" in name_lower:
        return "compressor"
    elif "exchanger" in name_lower or name_lower.startswith("e-"):
        return "heat_exchanger"
    elif "column" in name_lower or name_lower.startswith("c-"):
        return "column"
    elif "vessel" in name_lower or name_lower.startswith("v-"):
        return "vessel"
    elif "basin" in name_lower:
        return "basin"
    elif "building" in name_lower:
        return "building"
    elif "substation" in name_lower:
        return "substation"
    elif "mcc" in name_lower:
        return "mcc"
    elif "rack" in name_lower:
        return "pipe_rack"
    else:
        return "other"

# Extract envelope from shape bounding box
def get_envelope(shape):
    bbox = shape.BoundBox
    width = (bbox.XMax - bbox.XMin) * MM_TO_M
    length = (bbox.YMax - bbox.YMin) * MM_TO_M
    height = (bbox.ZMax - bbox.ZMin) * MM_TO_M

    # Check if roughly circular (width ~= length)
    if abs(width - length) < 0.1 * max(width, length):
        diameter = (width + length) / 2
        return {{"shape": "circle", "diameter": round(diameter, 3)}}, round(height, 3)
    else:
        return {{"shape": "rectangle", "width": round(width, 3), "length": round(length, 3)}}, round(height, 3)

# Extract equipment from all objects in document
equipment_prefix = "{equipment_prefix}"
for obj in doc.Objects:
    # Skip boundary object and non-shape objects
    if boundary_name and obj.Name == boundary_name:
        continue
    if not hasattr(obj, "Shape") or obj.Shape.isNull():
        continue

    # Skip if prefix specified and doesn't match
    if equipment_prefix and not obj.Name.startswith(equipment_prefix):
        continue

    # Skip Draft objects that aren't equipment (wires, dimensions, etc.)
    obj_type = obj.TypeId
    if obj_type in ["Draft::Wire", "Draft::Dimension", "Draft::Text", "Draft::Label"]:
        continue

    # Get envelope and type
    try:
        envelope, height = get_envelope(obj.Shape)
    except:
        continue

    equip_type = infer_equipment_type(obj.Name, obj_type)

    # Get placement (position in mm, convert to m)
    placement = obj.Placement
    pos_x = placement.Base.x * MM_TO_M
    pos_y = placement.Base.y * MM_TO_M
    base_elev = placement.Base.z * MM_TO_M

    # Get rotation around Z axis
    rotation_deg = 0
    if hasattr(placement.Rotation, "Angle"):
        axis = placement.Rotation.Axis
        if abs(axis.z) > 0.9:  # Rotation around Z
            rotation_deg = math.degrees(placement.Rotation.Angle)

    # Extract parameters from Spreadsheet if linked
    parameters = {{}}
    if hasattr(obj, "ExpressionEngine"):
        for prop, expr in obj.ExpressionEngine:
            parameters[prop] = expr

    equipment_item = {{
        "id": obj.Name,
        "type": equip_type,
        "envelope": envelope,
        "height": height,
        "base_elevation": round(base_elev, 3),
        "truth_ref": f"FreeCAD::{{doc.Name}}::{{obj.Name}}",
        "clearances": {{
            "maintenance": 2.0,
            "operation": 1.5
        }}
    }}

    # Add parameters if any
    if parameters:
        equipment_item["parameters"] = parameters

    result["equipment"].append(equipment_item)

# Compute content hash for reproducibility tracking
content_str = json.dumps(result["equipment"], sort_keys=True)
result["metadata"]["hash"] = "sha256:" + __import__("hashlib").sha256(content_str.encode()).hexdigest()[:16]

print(json.dumps(result))
'''

            res = freecad.execute_code(extract_code)

            if not res.get("success"):
                return [TextContent(type="text", text=f"Failed to extract contract: {res.get('error', 'Unknown error')}")]

            # Parse the JSON from the output
            output = res.get("message", "")

            # Find the JSON in the output - FreeCAD output may have prefix text
            # We need to find the first complete JSON object
            try:
                contract = _extract_json_from_output(output)
                if contract is None:
                    return [TextContent(type="text", text=f"Could not find valid JSON in output: {output}")]
            except json.JSONDecodeError as e:
                return [TextContent(type="text", text=f"Failed to parse contract JSON: {e}\nOutput: {output}")]

            # Write to file if path specified
            if output_path:
                # Convert WSL paths to Windows paths for cross-platform compatibility
                output_path = wsl_to_windows_path(output_path)

                # Ensure parent directory exists
                parent_dir = os.path.dirname(output_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

                with open(output_path, "w") as f:
                    json.dump(contract, f, indent=2)

                # Verify file was created
                if os.path.exists(output_path):
                    file_size = os.path.getsize(output_path)
                    msg = f"Contract exported to: {output_path} ({file_size} bytes)"
                else:
                    msg = f"Contract export failed: file not created at {output_path}"

                screenshot = freecad.get_active_screenshot()
                response = [
                    TextContent(type="text", text=f"{msg}\n"
                               f"Equipment count: {len(contract['equipment'])}\n"
                               f"Boundary points: {len(contract['site']['boundary'])}")
                ]
                return add_screenshot_if_available(response, screenshot, include_screenshot)
            else:
                # Return JSON in response, filtered by detail_level
                filtered_contract = filter_contract_response(contract, detail_level)
                screenshot = freecad.get_active_screenshot()
                response = [
                    TextContent(type="text", text=json.dumps(filtered_contract, indent=2))
                ]
                return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to export contract: {str(e)}")
            return [TextContent(type="text", text=f"Failed to export contract: {str(e)}")]

    @mcp.tool()
    def apply_placements(
        ctx: Context,
        doc_name: str,
        contract_json: str | dict | None = None,
        contract_path: str | None = None,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Apply solved placements from a Spatial Contract back to FreeCAD objects.

        Takes a contract JSON (with placements[] populated by site-fit solver) and
        updates the FreeCAD object positions accordingly. All dimensions are in meters
        and converted to mm for FreeCAD.

        Args:
            doc_name: Name of the FreeCAD document
            contract_json: Contract JSON string with placements (mutually exclusive with contract_path)
            contract_path: Path to contract JSON file (mutually exclusive with contract_json)

        Returns:
            Success/failure message with count of updated objects

        Examples:
            Apply from JSON string:
            ```json
            {
                "doc_name": "WWT_Plant",
                "contract_json": "{\\"placements\\": [{\\"id\\": \\"TK-101\\", \\"x\\": 45.2, \\"y\\": 78.1}]}"
            }
            ```

            Apply from file:
            ```json
            {
                "doc_name": "WWT_Plant",
                "contract_path": "/tmp/solved_contract.json"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Load contract from file, dict, or string
            if contract_path:
                with open(contract_path, "r") as f:
                    contract = json.load(f)
            elif contract_json:
                # Handle both dict (from MCP parsing) and string
                if isinstance(contract_json, dict):
                    contract = contract_json
                else:
                    contract = json.loads(contract_json)
            else:
                return [TextContent(type="text", text="Either contract_json or contract_path must be provided")]

            placements = contract.get("placements", [])
            if not placements:
                return [TextContent(type="text", text="No placements found in contract")]

            # Build code to apply placements
            apply_code = f'''
import FreeCAD
import math

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    raise ValueError("Document '{doc_name}' not found")

# Unit conversion (m to mm)
M_TO_MM = 1000.0

def find_equipment_by_id(doc, equip_id):
    """Find equipment by EquipmentId property first (survives name collisions)."""
    # First, try EquipmentId custom property
    for obj in doc.Objects:
        if getattr(obj, "EquipmentId", None) == equip_id:
            return obj
    # Fallback to normalized Name (FreeCAD converts hyphens to underscores)
    normalized_id = equip_id.replace("-", "_")
    obj = doc.getObject(normalized_id)
    if obj:
        return obj
    # Fallback to Label search
    matches = doc.getObjectsByLabel(equip_id)
    if len(matches) == 1:
        return matches[0]
    return None

placements = {json.dumps(placements)}
updated = []
errors = []

for p in placements:
    # Support both 'id' (contract format) and 'structure_id' (site-fit format)
    obj_id = p.get("id") or p.get("structure_id")
    if not obj_id:
        errors.append("Placement missing both 'id' and 'structure_id'")
        continue

    x = p.get("x", 0) * M_TO_MM  # Convert m to mm
    y = p.get("y", 0) * M_TO_MM
    rotation_deg = p.get("rotation_deg", 0)

    obj = find_equipment_by_id(doc, obj_id)
    if not obj:
        errors.append(f"Object '{{obj_id}}' not found")
        continue

    # Get current Z position to preserve elevation
    current_z = obj.Placement.Base.z

    # For Part::Box (rectangular equipment), dimensions are pre-swapped during creation
    # based on rotation_deg, so we use simple center-to-corner offset (no FreeCAD rotation)
    # Site-fit provides CENTER coordinates, but FreeCAD Part::Box uses CORNER as origin
    if obj.TypeId == "Part::Box":
        # FreeCAD Part::Box dimensions: Width=X, Length=Y, Height=Z
        # These are already swapped for 90/270 rotation during equipment creation
        half_x = obj.Width.Value / 2.0
        half_y = obj.Length.Value / 2.0

        # Simple center-to-corner offset (no rotation - dimensions pre-swapped)
        new_pos = FreeCAD.Vector(x - half_x, y - half_y, current_z)

        # No rotation needed - dimensions are pre-swapped based on rotation_deg
        obj.Placement = FreeCAD.Placement(new_pos, FreeCAD.Rotation())
    else:
        # Cylinders and other shapes are already centered
        # Apply rotation for non-rectangular shapes (though circles don't care about rotation)
        rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), rotation_deg)
        new_pos = FreeCAD.Vector(x, y, current_z)
        obj.Placement = FreeCAD.Placement(new_pos, rotation)
    updated.append(obj_id)

doc.recompute()

result = {{
    "updated": updated,
    "errors": errors
}}
print(__import__("json").dumps(result))
'''

            res = freecad.execute_code(apply_code)

            if not res.get("success"):
                return [TextContent(type="text", text=f"Failed to apply placements: {res.get('error', 'Unknown error')}")]

            # Parse result
            output = res.get("message", "")
            try:
                result = _extract_json_from_output(output)
                if result is None:
                    result = {"updated": [], "errors": [f"Could not parse output: {output}"]}
            except json.JSONDecodeError:
                result = {"updated": [], "errors": [f"JSON parse error: {output}"]}

            screenshot = freecad.get_active_screenshot()

            status_msg = f"Applied placements:\n- Updated: {len(result.get('updated', []))} objects"
            if result.get('errors'):
                status_msg += f"\n- Errors: {len(result['errors'])}"
                for err in result['errors'][:5]:  # Show first 5 errors
                    status_msg += f"\n  - {err}"

            response = [TextContent(type="text", text=status_msg)]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to apply placements: {str(e)}")
            return [TextContent(type="text", text=f"Failed to apply placements: {str(e)}")]

    @mcp.tool()
    def export_glb(
        ctx: Context,
        doc_name: str,
        object_name: str | None = None,
        output_path: str | None = None,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Export mesh in GLB/glTF format for Blender visualization.

        Exports a single object or entire document as GLB mesh. Object names are
        preserved for correlation with Spatial Contract equipment IDs.

        Args:
            doc_name: Name of the FreeCAD document
            object_name: Specific object to export (exports all if not specified)
            output_path: Output file path (uses temp dir if not specified)

        Returns:
            Path to exported GLB file

        Examples:
            Export single tank:
            ```json
            {
                "doc_name": "WWT_Plant",
                "object_name": "TK-101",
                "output_path": "/tmp/exports/TK-101.glb"
            }
            ```

            Export entire document:
            ```json
            {
                "doc_name": "WWT_Plant",
                "output_path": "/tmp/exports/plant.glb"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Determine output path
            if not output_path:
                output_dir = tempfile.gettempdir()
                filename = f"{object_name}.glb" if object_name else f"{doc_name}.glb"
                output_path = os.path.join(output_dir, filename)

            # Convert WSL paths to Windows paths for cross-platform compatibility
            output_path = wsl_to_windows_path(output_path)

            # Ensure output directory exists
            parent_dir = os.path.dirname(output_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            # Build export code
            obj_filter = f'obj_name = "{object_name}"' if object_name else 'obj_name = None'

            export_code = f'''
import FreeCAD
import Mesh
import os
import tempfile

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    raise ValueError("Document '{doc_name}' not found")

{obj_filter}
output_path = r"{output_path}"

# Get objects to export
if obj_name:
    obj = doc.getObject(obj_name)
    if not obj:
        raise ValueError(f"Object '{{obj_name}}' not found")
    objects = [obj]
else:
    # Export all objects with shapes
    objects = [o for o in doc.Objects if hasattr(o, "Shape") and not o.Shape.isNull()]

if not objects:
    raise ValueError("No exportable objects found")

# Create mesh from shapes
meshes = []
for obj in objects:
    try:
        # Tessellate shape to mesh
        shape = obj.Shape
        mesh = Mesh.Mesh()
        # Get tessellation with reasonable detail
        tessellation = shape.tessellate(1.0)  # 1mm tolerance
        if tessellation[0] and tessellation[1]:
            mesh.addFacets(tessellation)
            meshes.append(mesh)
    except Exception as e:
        print(f"Warning: Could not mesh {{obj.Name}}: {{e}}")

if not meshes:
    raise ValueError("Could not create any meshes from objects")

# Combine all meshes
combined = Mesh.Mesh()
for m in meshes:
    combined.addMesh(m)

# Export to GLB (FreeCAD exports to glTF/GLB via Mesh workbench)
# Note: FreeCAD's native export might be OBJ/STL, may need addon for GLB
# For now, export as OBJ and note that conversion may be needed
obj_path = output_path.replace(".glb", ".obj")
combined.write(obj_path)

# Verify file was created
if os.path.exists(obj_path):
    file_size = os.path.getsize(obj_path)
    print(f"Exported to: {{obj_path}} ({{file_size}} bytes)")
else:
    print(f"Export failed: file not created at {{obj_path}}")
print(f"Vertices: {{combined.CountPoints}}")
print(f"Faces: {{combined.CountFacets}}")
'''

            res = freecad.execute_code(export_code)

            if not res.get("success"):
                return [TextContent(type="text", text=f"Failed to export: {res.get('error', 'Unknown error')}")]

            output = res.get("message", "")

            # Note: FreeCAD may export OBJ instead of GLB natively
            # The actual path might be .obj
            actual_path = output_path.replace(".glb", ".obj") if ".glb" in output_path else output_path

            screenshot = freecad.get_active_screenshot()
            response = [
                TextContent(type="text", text=f"Mesh exported:\n{output}\n\n"
                           f"Note: FreeCAD exports OBJ natively. For GLB conversion, use:\n"
                           f"  blender --background --python-expr \"import bpy; bpy.ops.import_scene.obj(filepath='{actual_path}'); bpy.ops.export_scene.gltf(filepath='{output_path}')\"")
            ]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to export GLB: {str(e)}")
            return [TextContent(type="text", text=f"Failed to export: {str(e)}")]

    @mcp.tool()
    def create_equipment_envelope(
        ctx: Context,
        doc_name: str,
        equipment_id: str,
        equipment_type: str,
        shape: str,
        width: float | None = None,
        length: float | None = None,
        diameter: float | None = None,
        height: float = 5.0,
        rotation_deg: int = 0,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Create an equipment envelope placeholder in FreeCAD.

        Creates a simple 3D shape representing equipment footprint for site layout.
        All dimensions are in meters.

        Args:
            doc_name: Name of the FreeCAD document
            equipment_id: Equipment tag (e.g., "TK-101")
            equipment_type: Type of equipment (for metadata)
            shape: Envelope shape ("rectangle" or "circle")
            width: Width in meters (for rectangle)
            length: Length in meters (for rectangle)
            diameter: Diameter in meters (for circle)
            height: Height in meters
            rotation_deg: Rotation in degrees (0, 90, 180, 270) - swaps width/length for 90/270

        Returns:
            Success message with object details

        Examples:
            Create circular tank:
            ```json
            {
                "doc_name": "WWT_Plant",
                "equipment_id": "TK-101",
                "equipment_type": "storage_tank",
                "shape": "circle",
                "diameter": 12.0,
                "height": 8.5
            }
            ```

            Create rectangular building:
            ```json
            {
                "doc_name": "WWT_Plant",
                "equipment_id": "BLDG-001",
                "equipment_type": "building",
                "shape": "rectangle",
                "width": 20.0,
                "length": 30.0,
                "height": 6.0
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Convert meters to mm for FreeCAD
            height_mm = height * M_TO_MM

            # Digester types that get dome covers
            digester_types = ["digester", "anaerobic_digester", "anmbr", "gas_holder"]
            # Building types that get flat roofs
            building_types = ["building", "control_building", "biogas_building", "pump_station",
                             "blower_building", "mcc_building", "dewatering_building", "uv_building",
                             "chemical_building", "screen_building"]

            if shape == "circle":
                if not diameter:
                    return [TextContent(type="text", text="diameter is required for circle shape")]
                radius_mm = (diameter / 2) * M_TO_MM

                # Check if this is a digester type that needs a dome cover
                is_digester = equipment_type.lower() in digester_types

                if is_digester:
                    # Digester with dome cover
                    # Dome height ratio: 6m cover / 40m diameter = 0.15
                    DOME_RATIO = 0.15
                    dome_height_mm = diameter * DOME_RATIO * M_TO_MM
                    tank_height_mm = height_mm  # height parameter IS the shell height; dome added on top

                    create_code = f'''
import FreeCAD
import Part
import math

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

# Create tank body (cylinder)
tank = doc.addObject("Part::Cylinder", "{equipment_id}_tank")
tank.Radius = {radius_mm}
tank.Height = {tank_height_mm}
tank.Label = "{equipment_id}_tank"

# Create dome cover using Part::Ellipsoid (flattened hemisphere)
# Radius1 = Z height, Radius2 = X radius, Radius3 = Y radius
dome = doc.addObject("Part::Ellipsoid", "{equipment_id}_dome")
dome.Radius1 = {dome_height_mm}  # Z-direction (dome height)
dome.Radius2 = {radius_mm}       # X-direction (horizontal radius)
dome.Radius3 = {radius_mm}       # Y-direction (horizontal radius)
dome.Angle1 = 0                  # Start at equator
dome.Angle2 = 90                 # End at top (hemisphere)
dome.Angle3 = 360                # Full rotation
dome.Label = "{equipment_id}_dome"

# Position dome on top of tank
dome.Placement.Base.z = {tank_height_mm}

# Create compound to group them
compound = doc.addObject("Part::Compound", "{equipment_id}")
compound.Links = [tank, dome]
compound.Label = "{equipment_id}"

# Add metadata as properties
compound.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
compound.EquipmentType = "{equipment_type}"
compound.addProperty("App::PropertyFloat", "DiameterM", "ProcessEng", "Diameter in meters")
compound.DiameterM = {diameter}
compound.addProperty("App::PropertyFloat", "HeightM", "ProcessEng", "Total height in meters")
compound.HeightM = {height}
compound.addProperty("App::PropertyFloat", "DomeHeightM", "ProcessEng", "Dome height in meters")
compound.DomeHeightM = {diameter * DOME_RATIO}

doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print(f"Created {{compound.Name}} ({{compound.Label}}) with dome cover")
'''
                else:
                    # Standard circular tank (no dome)
                    create_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

# Create cylinder for circular tank
cylinder = doc.addObject("Part::Cylinder", "{equipment_id}")
cylinder.Radius = {radius_mm}
cylinder.Height = {height_mm}
cylinder.Label = "{equipment_id}"

# Add metadata as properties
cylinder.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
cylinder.EquipmentType = "{equipment_type}"
cylinder.addProperty("App::PropertyFloat", "DiameterM", "ProcessEng", "Diameter in meters")
cylinder.DiameterM = {diameter}
cylinder.addProperty("App::PropertyFloat", "HeightM", "ProcessEng", "Height in meters")
cylinder.HeightM = {height}

doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print(f"Created {{cylinder.Name}} ({{cylinder.Label}})")
'''
            elif shape == "rectangle":
                if not width or not length:
                    return [TextContent(type="text", text="width and length are required for rectangle shape")]

                # Pre-swap dimensions for 90/270 rotation (no FreeCAD rotation needed)
                if rotation_deg in (90, 270):
                    eff_width, eff_length = length, width
                else:
                    eff_width, eff_length = width, length

                width_mm = eff_width * M_TO_MM
                length_mm = eff_length * M_TO_MM

                # Check if this is a building type that needs a roof
                is_building = equipment_type.lower() in building_types

                if is_building:
                    # Building with flat roof and overhang
                    ROOF_THICKNESS_MM = 300  # 300mm roof slab
                    OVERHANG_MM = 200  # 200mm overhang
                    wall_height_mm = height_mm - ROOF_THICKNESS_MM

                    create_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

# Create walls (main building body)
walls = doc.addObject("Part::Box", "{equipment_id}_walls")
walls.Width = {width_mm}    # X dimension (matches contract w)
walls.Length = {length_mm}  # Y dimension (matches contract h)
walls.Height = {wall_height_mm}
walls.Label = "{equipment_id}_walls"

# Center walls on origin
walls.Placement.Base.x = -{width_mm / 2}
walls.Placement.Base.y = -{length_mm / 2}

# Create flat roof slab with overhang
roof = doc.addObject("Part::Box", "{equipment_id}_roof")
roof.Width = {width_mm} + 2 * {OVERHANG_MM}    # X dimension
roof.Length = {length_mm} + 2 * {OVERHANG_MM}  # Y dimension
roof.Height = {ROOF_THICKNESS_MM}
roof.Label = "{equipment_id}_roof"

# Position roof on top of walls, centered with overhang
roof.Placement.Base.x = -{width_mm / 2} - {OVERHANG_MM}
roof.Placement.Base.y = -{length_mm / 2} - {OVERHANG_MM}
roof.Placement.Base.z = {wall_height_mm}

# Create compound to group them
compound = doc.addObject("Part::Compound", "{equipment_id}")
compound.Links = [walls, roof]
compound.Label = "{equipment_id}"

# Add metadata as properties
compound.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
compound.EquipmentType = "{equipment_type}"
compound.addProperty("App::PropertyFloat", "WidthM", "ProcessEng", "Width in meters")
compound.WidthM = {width}
compound.addProperty("App::PropertyFloat", "LengthM", "ProcessEng", "Length in meters")
compound.LengthM = {length}
compound.addProperty("App::PropertyFloat", "HeightM", "ProcessEng", "Height in meters")
compound.HeightM = {height}

doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print(f"Created {{compound.Name}} ({{compound.Label}}) with flat roof")
'''
                else:
                    # Standard rectangular equipment (no roof)
                    create_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

# Create box for rectangular equipment
box = doc.addObject("Part::Box", "{equipment_id}")
box.Width = {width_mm}    # X dimension (matches contract w)
box.Length = {length_mm}  # Y dimension (matches contract h)
box.Height = {height_mm}
box.Label = "{equipment_id}"

# Center the box on origin (FreeCAD boxes start at corner)
box.Placement.Base.x = -{width_mm / 2}
box.Placement.Base.y = -{length_mm / 2}

# Add metadata as properties
box.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
box.EquipmentType = "{equipment_type}"
box.addProperty("App::PropertyFloat", "WidthM", "ProcessEng", "Width in meters")
box.WidthM = {width}
box.addProperty("App::PropertyFloat", "LengthM", "ProcessEng", "Length in meters")
box.LengthM = {length}
box.addProperty("App::PropertyFloat", "HeightM", "ProcessEng", "Height in meters")
box.HeightM = {height}

doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print(f"Created {{box.Name}} ({{box.Label}})")
'''
            else:
                return [TextContent(type="text", text=f"Unknown shape: {shape}. Use 'rectangle' or 'circle'")]

            res = freecad.execute_code(create_code)

            if not res.get("success"):
                return [TextContent(type="text", text=f"Failed to create envelope: {res.get('error', 'Unknown error')}")]

            screenshot = freecad.get_active_screenshot()

            dims = f"diameter={diameter}m" if shape == "circle" else f"width={width}m, length={length}m"
            response = [
                TextContent(type="text", text=f"Created equipment envelope:\n"
                           f"  ID: {equipment_id}\n"
                           f"  Type: {equipment_type}\n"
                           f"  Shape: {shape}\n"
                           f"  Dimensions: {dims}, height={height}m")
            ]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to create envelope: {str(e)}")
            return [TextContent(type="text", text=f"Failed to create envelope: {str(e)}")]

    @mcp.tool()
    def create_site_boundary(
        ctx: Context,
        doc_name: str,
        boundary_points: list[list[float]],
        boundary_name: str = "SiteBoundary",
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Create a site boundary polyline in FreeCAD.

        Creates a Draft Wire representing the site boundary for export to Spatial Contract.
        All coordinates are in meters.

        Args:
            doc_name: Name of the FreeCAD document
            boundary_points: List of [x, y] coordinates in meters defining the boundary polygon
            boundary_name: Name for the boundary object

        Returns:
            Success message with boundary details

        Examples:
            Create rectangular site:
            ```json
            {
                "doc_name": "WWT_Plant",
                "boundary_points": [[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]],
                "boundary_name": "SiteBoundary"
            }
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Convert points to mm for FreeCAD
            points_mm = [[p[0] * M_TO_MM, p[1] * M_TO_MM] for p in boundary_points]

            create_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")

points = {json.dumps(points_mm)}
vectors = [FreeCAD.Vector(p[0], p[1], 0) for p in points]

# Create Draft Wire
wire = Draft.makeWire(vectors, closed=True, face=False)
wire.Label = "{boundary_name}"

# Style the boundary
if hasattr(wire.ViewObject, "LineColor"):
    wire.ViewObject.LineColor = (0.0, 0.5, 0.0)  # Green
if hasattr(wire.ViewObject, "LineWidth"):
    wire.ViewObject.LineWidth = 3.0

doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.viewTop()
FreeCADGui.ActiveDocument.ActiveView.fitAll()

print(f"Created boundary '{{wire.Label}}' with {{len(vectors)}} points")
'''

            res = freecad.execute_code(create_code)

            if not res.get("success"):
                return [TextContent(type="text", text=f"Failed to create boundary: {res.get('error', 'Unknown error')}")]

            screenshot = freecad.get_active_screenshot()
            response = [
                TextContent(type="text", text=f"Created site boundary:\n"
                           f"  Name: {boundary_name}\n"
                           f"  Points: {len(boundary_points)}\n"
                           f"  Closed: Yes")
            ]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to create boundary: {str(e)}")
            return [TextContent(type="text", text=f"Failed to create boundary: {str(e)}")]

    @mcp.tool()
    def import_sitefit_contract(
        ctx: Context,
        doc_name: str,
        contract_json: str | dict,
        create_boundary: bool = True,
        create_roads: bool = True,
        create_equipment: bool = True,
        apply_placements_flag: bool = True,
        road_layer_name: str = "RoadCenterlines",
        strict: bool = False,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Import a site-fit contract and create FreeCAD document with all components.

        This is the primary integration tool for importing site-fit solutions into FreeCAD.
        It creates the document (if needed), site boundary, equipment envelopes, applies
        placements, and creates road centerlines - all in one call.

        Args:
            doc_name: Name for the FreeCAD document (created if doesn't exist)
            contract_json: Contract JSON from sitefit_export_contract (string or dict)
            create_boundary: Create site boundary Draft Wire (default: True)
            create_roads: Create road centerlines as Draft Wires (default: True)
            create_equipment: Create equipment envelopes (default: True)
            apply_placements_flag: Apply solved placements to equipment (default: True)
            road_layer_name: Group name for road centerlines (default: "RoadCenterlines")
            strict: Fail immediately on first equipment creation error (default: False)

        Returns:
            Summary of imported components

        Examples:
            Import full contract:
            ```python
            # First get contract from site-fit
            contract = sitefit_export_contract(solution_id="sol_001")

            # Then import into FreeCAD
            import_sitefit_contract(
                doc_name="SitePlan",
                contract_json=contract["data"]
            )
            ```
        """
        freecad = get_freecad_connection()

        try:
            # Parse contract
            if isinstance(contract_json, str):
                contract = json.loads(contract_json)
            else:
                contract = contract_json

            # Extract data from contract
            site_data = contract.get("site", {})
            program_data = contract.get("program", {})
            placements_data = contract.get("placements", [])
            road_network = contract.get("road_network")

            structures = program_data.get("structures", [])
            boundary = site_data.get("boundary", [])

            # Track results
            results = {
                "document": False,
                "boundary": 0,
                "equipment": 0,
                "placements": 0,
                "roads": 0,
                "errors": []
            }

            # 1. Create document if it doesn't exist (with structured sentinel)
            doc_check = f'''
import FreeCAD
doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")
    print("DOC_STATUS:created")
else:
    print("DOC_STATUS:exists")
'''
            res = freecad.execute_code(doc_check)
            msg = res.get("message", "")
            if not res.get("success", True) or "DOC_STATUS:" not in msg:
                results["document"] = False
                error_detail = res.get("error", msg or "Unknown error")
                results["errors"].append(f"Failed to create/find document: {error_detail}")
                return [TextContent(type="text", text=f"Document creation failed: {error_detail}")]
            results["document"] = True

            # 2. Create boundary if requested
            if create_boundary and boundary:
                points_mm = [[p[0] * M_TO_MM, p[1] * M_TO_MM] for p in boundary]
                boundary_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")
points = {json.dumps(points_mm)}
vectors = [FreeCAD.Vector(p[0], p[1], 0) for p in points]
wire = Draft.makeWire(vectors, closed=True, face=False)
wire.Label = "SiteBoundary"
if hasattr(wire.ViewObject, "LineColor"):
    wire.ViewObject.LineColor = (0.0, 0.5, 0.0)
if hasattr(wire.ViewObject, "LineWidth"):
    wire.ViewObject.LineWidth = 3.0
doc.recompute()
print("boundary_ok")
'''
                res = freecad.execute_code(boundary_code)
                if "boundary_ok" in res.get("message", ""):
                    results["boundary"] = 1

            # 3. Create equipment envelopes if requested
            # Digester types that get dome covers (same list as create_equipment_envelope)
            digester_types = ["digester", "anaerobic_digester", "reactor", "cstr",
                              "uasb", "egsb", "ic_reactor", "membrane_bioreactor"]
            DOME_RATIO = 0.15  # Fallback: 6m cover / 40m diameter

            if create_equipment and structures:
                for struct in structures:
                    struct_id = struct.get("id", "")
                    struct_type = struct.get("type", "unknown")
                    footprint = struct.get("footprint", {})
                    struct_height = struct.get("height", 5.0)
                    dome_height_m = struct.get("dome_height_m")  # May be None
                    shape_type = footprint.get("shape", "rect")

                    if shape_type == "circle":
                        diameter = footprint.get("d", 10.0)
                        radius_mm = (diameter / 2) * M_TO_MM
                        height_mm = struct_height * M_TO_MM

                        # Check if this is a digester type that needs a dome cover
                        is_digester = struct_type.lower() in digester_types

                        if is_digester:
                            # Determine dome height: prefer explicit, fallback to ratio
                            if dome_height_m is not None:
                                dome_height_mm = dome_height_m * M_TO_MM
                            else:
                                dome_height_mm = diameter * DOME_RATIO * M_TO_MM

                            tank_height_mm = height_mm  # height parameter IS the shell height; dome added on top

                            equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")

# Check for existing equipment by EquipmentId property first, then by Name
existing = None
for obj in doc.Objects:
    if getattr(obj, "EquipmentId", None) == "{struct_id}":
        existing = obj
        break
if not existing:
    existing = doc.getObject("{struct_id}")

if existing:
    print("EQUIP_STATUS:exists:{struct_id}:" + existing.Name)
else:
    # Create tank body (cylinder)
    tank = doc.addObject("Part::Cylinder", "{struct_id}_tank")
    tank.Radius = {radius_mm}
    tank.Height = {tank_height_mm}
    tank.Label = "{struct_id}_tank"

    # Create dome cover using Part::Ellipsoid (flattened hemisphere)
    # Radius1 = Z height, Radius2 = X radius, Radius3 = Y radius
    dome = doc.addObject("Part::Ellipsoid", "{struct_id}_dome")
    dome.Radius1 = {dome_height_mm}  # Z-direction (dome height)
    dome.Radius2 = {radius_mm}       # X-direction (horizontal radius)
    dome.Radius3 = {radius_mm}       # Y-direction (horizontal radius)
    dome.Angle1 = 0                  # Start at equator
    dome.Angle2 = 90                 # End at top (hemisphere)
    dome.Angle3 = 360                # Full rotation
    dome.Label = "{struct_id}_dome"

    # Position dome on top of tank
    dome.Placement.Base.z = {tank_height_mm}

    # Create compound to group tank + dome
    compound = doc.addObject("Part::Compound", "{struct_id}")
    compound.Links = [tank, dome]
    compound.Label = "{struct_id}"

    # Add EquipmentId for stable lookup (on compound, not individual parts)
    try:
        compound.addProperty("App::PropertyString", "EquipmentId", "ProcessEng", "Stable equipment ID")
    except Exception:
        pass
    compound.EquipmentId = "{struct_id}"

    try:
        compound.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
    except Exception:
        pass
    compound.EquipmentType = "{struct_type}"

    doc.recompute()
    print("EQUIP_STATUS:created:{struct_id}:" + compound.Name)
'''
                        else:
                            # Non-digester circular equipment: simple cylinder
                            equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")

# Check for existing equipment by EquipmentId property first, then by Name
existing = None
for obj in doc.Objects:
    if getattr(obj, "EquipmentId", None) == "{struct_id}":
        existing = obj
        break
if not existing:
    existing = doc.getObject("{struct_id}")

if existing:
    print("EQUIP_STATUS:exists:{struct_id}:" + existing.Name)
else:
    cylinder = doc.addObject("Part::Cylinder", "{struct_id}")
    # Accept the object regardless of auto-rename (FreeCAD adds suffix on collision)
    actual_name = cylinder.Name
    cylinder.Radius = {radius_mm}
    cylinder.Height = {height_mm}
    cylinder.Label = "{struct_id}"  # Display name

    # Add EquipmentId for stable lookup (survives name collisions)
    try:
        cylinder.addProperty("App::PropertyString", "EquipmentId", "ProcessEng", "Stable equipment ID")
    except Exception:
        pass  # Property may already exist
    cylinder.EquipmentId = "{struct_id}"

    try:
        cylinder.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
    except Exception:
        pass  # Property may already exist
    cylinder.EquipmentType = "{struct_type}"
    doc.recompute()

    if actual_name != "{struct_id}":
        print("EQUIP_STATUS:created_renamed:{struct_id}:" + actual_name)
    else:
        print("EQUIP_STATUS:created:{struct_id}:" + actual_name)
'''
                    else:  # rectangle
                        orig_w = footprint.get("w", 10.0)
                        orig_h = footprint.get("h", 10.0)

                        # Look up placement to get rotation_deg for dimension swapping
                        placement = next(
                            (p for p in placements_data if p.get("id") == struct_id),
                            None
                        )
                        rotation_deg = placement.get("rotation_deg", 0) if placement else 0

                        # Pre-swap dimensions for 90/270 rotation (no FreeCAD rotation needed)
                        width, length = get_rect_dims_at_rotation(orig_w, orig_h, rotation_deg)
                        width_mm = width * M_TO_MM
                        length_mm = length * M_TO_MM
                        height_mm = struct_height * M_TO_MM

                        equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")

# Check for existing equipment by EquipmentId property first, then by Name
existing = None
for obj in doc.Objects:
    if getattr(obj, "EquipmentId", None) == "{struct_id}":
        existing = obj
        break
if not existing:
    existing = doc.getObject("{struct_id}")

if existing:
    print("EQUIP_STATUS:exists:{struct_id}:" + existing.Name)
else:
    box = doc.addObject("Part::Box", "{struct_id}")
    # Accept the object regardless of auto-rename (FreeCAD adds suffix on collision)
    actual_name = box.Name
    box.Width = {width_mm}   # Pre-swapped for rotation
    box.Length = {length_mm}  # Pre-swapped for rotation
    box.Height = {height_mm}
    box.Label = "{struct_id}"  # Display name
    # NOTE: Dimensions are pre-swapped based on rotation_deg, so no Placement.Rotation needed
    # apply_placements handles center-to-corner conversion using swapped dimensions

    # Add EquipmentId for stable lookup (survives name collisions)
    try:
        box.addProperty("App::PropertyString", "EquipmentId", "ProcessEng", "Stable equipment ID")
    except Exception:
        pass  # Property may already exist
    box.EquipmentId = "{struct_id}"

    try:
        box.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
    except Exception:
        pass  # Property may already exist
    box.EquipmentType = "{struct_type}"
    doc.recompute()

    if actual_name != "{struct_id}":
        print("EQUIP_STATUS:created_renamed:{struct_id}:" + actual_name)
    else:
        print("EQUIP_STATUS:created:{struct_id}:" + actual_name)
'''

                    res = freecad.execute_code(equip_code)
                    msg = res.get("message", "")
                    if "EQUIP_STATUS:created" in msg or "EQUIP_STATUS:exists" in msg:
                        results["equipment"] += 1
                        # Log if object was auto-renamed
                        if "created_renamed" in msg:
                            logger.info(f"Equipment {struct_id} was auto-renamed by FreeCAD due to name collision")
                    else:
                        error_detail = res.get("error", msg or "Unknown error")
                        results["errors"].append(f"Failed to create {struct_id}: {error_detail}")
                        logger.error(f"Equipment creation failed for {struct_id}: {error_detail}")

                        # Fail fast in strict mode
                        if strict:
                            return [TextContent(
                                type="text",
                                text=f"Equipment creation failed (strict mode):\n"
                                     f"  Failed: {struct_id}\n"
                                     f"  Error: {error_detail}\n"
                                     f"  Created: {results['equipment']} of {len(structures)}\n"
                                     f"Use strict=False to continue on errors."
                            )]

            # 4. Apply placements if requested
            if apply_placements_flag and placements_data:
                placement_code = f'''
import FreeCAD
import math

doc = FreeCAD.getDocument("{doc_name}")
M_TO_MM = 1000.0
placements = {json.dumps(placements_data)}
updated = 0
not_found = []

def find_equipment_by_id(doc, equip_id):
    """Find equipment by EquipmentId property (survives name collisions)."""
    # First, try to find by EquipmentId custom property
    for obj in doc.Objects:
        if getattr(obj, "EquipmentId", None) == equip_id:
            return obj
    # Fallback to normalized Name (FreeCAD converts hyphens to underscores)
    normalized_id = equip_id.replace("-", "_")
    obj = doc.getObject(normalized_id)
    if obj:
        return obj
    # Fallback to Label search
    matches = doc.getObjectsByLabel(equip_id)
    if len(matches) == 1:
        return matches[0]
    return None

for p in placements:
    # Support both 'id' (contract format) and 'structure_id' (site-fit format)
    obj_id = p.get("id") or p.get("structure_id")
    if not obj_id:
        not_found.append("missing_id")
        continue

    x = p.get("x", 0) * M_TO_MM
    y = p.get("y", 0) * M_TO_MM
    rotation_deg = p.get("rotation_deg", 0)

    obj = find_equipment_by_id(doc, obj_id)
    if not obj:
        not_found.append(obj_id)
        continue

    current_z = obj.Placement.Base.z

    # Create rotation around Z axis
    rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), rotation_deg)

    # For Part::Box (rectangular equipment), calculate corner position
    # Site-fit provides CENTER coordinates, but FreeCAD Part::Box uses CORNER as origin
    # FreeCAD Placement rotation happens around Placement.Base (the corner), not geometric center
    # Formula: Base = center_world - R * center_local
    if obj.TypeId == "Part::Box":
        # FreeCAD Part::Box dimensions: Width=X, Length=Y, Height=Z
        half_x = obj.Width.Value / 2.0
        half_y = obj.Length.Value / 2.0
        center_local = FreeCAD.Vector(half_x, half_y, 0)

        # Compute base so that center ends up at (x, y) after rotation
        center_world = FreeCAD.Vector(x, y, current_z)
        new_pos = center_world - rotation.multVec(center_local)
    else:
        # Cylinders and other shapes are already centered
        new_pos = FreeCAD.Vector(x, y, current_z)

    obj.Placement = FreeCAD.Placement(new_pos, rotation)
    updated += 1

doc.recompute()
if not_found:
    print(f"placements_{{updated}}_missing_" + ",".join(not_found))
else:
    print(f"placements_{{updated}}")
'''
                res = freecad.execute_code(placement_code)
                msg = res.get("message", "")
                if "placements_" in msg:
                    try:
                        parts = msg.split("placements_")[1]
                        count = int(parts.split("_")[0].split()[0])
                        results["placements"] = count

                        # Check for missing equipment
                        if "_missing_" in msg:
                            missing = msg.split("_missing_")[1].strip()
                            if missing:
                                results["errors"].append(f"Placements skipped for missing equipment: {missing}")
                                if strict:
                                    logger.warning(f"Strict mode: {len(missing.split(','))} equipment not found for placement")
                    except (ValueError, IndexError):
                        pass

            # 5. Create road centerlines if requested
            if create_roads and road_network and road_network.get("segments"):
                segments = road_network["segments"]

                # Create a group for roads
                group_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")
group = doc.addObject("App::DocumentObjectGroup", "{road_layer_name}")
group.Label = "{road_layer_name}"
doc.recompute()
print("group_ok")
'''
                freecad.execute_code(group_code)

                for seg in segments:
                    seg_id = seg.get("id", "road")
                    start = seg.get("start", [0, 0])
                    end = seg.get("end", [0, 0])
                    waypoints = seg.get("waypoints", [])

                    # Build point list: start + waypoints + end
                    all_points = [start] + waypoints + [end]
                    points_mm = [[p[0] * M_TO_MM, p[1] * M_TO_MM] for p in all_points]

                    road_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")
points = {json.dumps(points_mm)}
vectors = [FreeCAD.Vector(p[0], p[1], 0) for p in points]
wire = Draft.makeWire(vectors, closed=False, face=False)
wire.Label = "{seg_id}"
if hasattr(wire.ViewObject, "LineColor"):
    wire.ViewObject.LineColor = (0.5, 0.5, 0.5)  # Gray
if hasattr(wire.ViewObject, "LineWidth"):
    wire.ViewObject.LineWidth = 2.0

# Add to group
group = doc.getObject("{road_layer_name}")
if group:
    group.addObject(wire)

doc.recompute()
print("road_ok")
'''
                    res = freecad.execute_code(road_code)
                    if "road_ok" in res.get("message", ""):
                        results["roads"] += 1

            # Final view adjustment
            view_code = f'''
import FreeCAD
import FreeCADGui

doc = FreeCAD.getDocument("{doc_name}")
doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.viewTop()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print("view_ok")
'''
            freecad.execute_code(view_code)

            # Build summary
            summary_parts = [f"Imported site-fit contract into '{doc_name}':"]
            if results["boundary"]:
                summary_parts.append(f"  - Boundary: 1")
            if results["equipment"]:
                summary_parts.append(f"  - Equipment: {results['equipment']}")
            if results["placements"]:
                summary_parts.append(f"  - Placements applied: {results['placements']}")
            if results["roads"]:
                summary_parts.append(f"  - Road segments: {results['roads']}")
            if results["errors"]:
                summary_parts.append(f"  - Errors: {len(results['errors'])}")
                for err in results["errors"][:3]:
                    summary_parts.append(f"    - {err}")

            screenshot = freecad.get_active_screenshot()
            response = [TextContent(type="text", text="\n".join(summary_parts))]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except json.JSONDecodeError as e:
            return [TextContent(type="text", text=f"Failed to parse contract JSON: {str(e)}")]
        except Exception as e:
            logger.error(f"Failed to import contract: {str(e)}")
            return [TextContent(type="text", text=f"Failed to import contract: {str(e)}")]

    @mcp.tool()
    async def import_solutions_as_layers(
        doc_name: str,
        solutions: list[dict],
        site_boundary: list[list[float]] | None = None,
        keepouts: list[dict] | None = None,
        active_layer_index: int = 0,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
        ctx: Context = None,
    ) -> list[TextContent | ImageContent]:
        """Import multiple site-fit solutions as toggleable layers in a single document.

        Creates a layer structure for easy visibility toggling:
        - Common layer: site boundary, keepouts (always visible)
        - Solution layers: equipment + roads for each solution

        Args:
            doc_name: Name for the FreeCAD document (created if doesn't exist)
            solutions: List of solution dicts, each with:
                - solution_id: str
                - rank: int
                - placements: list of {structure_id, x, y, rotation_deg}
                - structures: list of structure definitions
            site_boundary: Site boundary [[x,y], ...] in meters
            keepouts: Optional keepout zones
            active_layer_index: Which solution layer is visible by default (0-indexed)

        Returns:
            Summary with layer names and visibility toggle instructions

        Example:
            import_solutions_as_layers(
                doc_name="SitePlan",
                solutions=[
                    {"solution_id": "sol_001", "rank": 1, "placements": [...], "structures": [...]},
                    {"solution_id": "sol_002", "rank": 2, "placements": [...], "structures": [...]}
                ],
                site_boundary=[[0,0], [150,0], [150,100], [0,100], [0,0]],
                active_layer_index=0
            )
        """
        freecad = get_freecad_connection()

        try:
            # 1. Create or get document
            doc_code = f'''
import FreeCAD
doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    doc = FreeCAD.newDocument("{doc_name}")
    print("DOC_STATUS:created")
else:
    print("DOC_STATUS:exists")
'''
            freecad.execute_code(doc_code)

            # 2. Create Common layer with boundary
            common_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")

# Create Common group for shared elements
common_group = doc.addObject("App::DocumentObjectGroup", "Common")
common_group.Label = "Common"
'''
            if site_boundary:
                points_mm = [[p[0] * M_TO_MM, p[1] * M_TO_MM] for p in site_boundary]
                common_code += f'''
# Create site boundary
points = [FreeCAD.Vector(p[0], p[1], 0) for p in {points_mm}]
wire = Draft.make_wire(points, closed=True, face=False)
wire.Label = "SiteBoundary"
common_group.addObject(wire)
'''
            common_code += '''
doc.recompute()
print("common_ok")
'''
            freecad.execute_code(common_code)

            # 3. Create a layer for each solution
            created_layers = []
            for idx, sol in enumerate(solutions):
                sol_id = sol.get("solution_id", f"sol_{idx}")
                rank = sol.get("rank", idx + 1)
                placements = sol.get("placements", [])
                structures = sol.get("structures", [])
                layer_name = f"Layout_{idx + 1}_Rank{rank}"
                is_active = (idx == active_layer_index)

                # Create layer group
                layer_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")

# Create layer group
layer = doc.addObject("App::DocumentObjectGroup", "{layer_name}")
layer.Label = "{layer_name}"

# Create equipment subgroup
equip_group = doc.addObject("App::DocumentObjectGroup", "{layer_name}_Equipment")
equip_group.Label = "Equipment"
layer.addObject(equip_group)

doc.recompute()
print("layer_ok")
'''
                freecad.execute_code(layer_code)

                # Create equipment for this layer
                for struct in structures:
                    struct_id = struct.get("id", "Unknown")
                    struct_type = struct.get("type", "equipment")
                    footprint = struct.get("footprint", {})
                    shape = footprint.get("shape", "rect")
                    struct_height = struct.get("height", 5.0)
                    height_mm = struct_height * M_TO_MM

                    # Find placement for this structure
                    placement = next(
                        (p for p in placements if p.get("structure_id") == struct_id or p.get("id") == struct_id),
                        None
                    )
                    x_m = placement.get("x", 0) if placement else 0
                    y_m = placement.get("y", 0) if placement else 0
                    rotation_deg = placement.get("rotation_deg", 0) if placement else 0
                    x_mm = x_m * M_TO_MM
                    y_mm = y_m * M_TO_MM

                    # Unique object name for this layer
                    obj_name = f"{struct_id.replace('-', '_')}_L{idx + 1}"

                    if shape == "circle":
                        diameter = footprint.get("d", 10.0)
                        radius_mm = (diameter / 2) * M_TO_MM
                        equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
layer = doc.getObject("{layer_name}")
equip_group = doc.getObject("{layer_name}_Equipment")

cyl = doc.addObject("Part::Cylinder", "{obj_name}")
cyl.Radius = {radius_mm}
cyl.Height = {height_mm}
cyl.Label = "{struct_id}"
cyl.Placement = FreeCAD.Placement(FreeCAD.Vector({x_mm}, {y_mm}, 0), FreeCAD.Rotation())
equip_group.addObject(cyl)
doc.recompute()
'''
                    else:  # rectangle
                        orig_w = footprint.get("w", 10.0)
                        orig_h = footprint.get("h", 10.0)

                        # Pre-swap dimensions for 90/270 rotation
                        if rotation_deg in (90, 270):
                            width, length = orig_h, orig_w
                        else:
                            width, length = orig_w, orig_h

                        width_mm = width * M_TO_MM
                        length_mm = length * M_TO_MM
                        half_x = width_mm / 2.0
                        half_y = length_mm / 2.0
                        corner_x = x_mm - half_x
                        corner_y = y_mm - half_y

                        equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
layer = doc.getObject("{layer_name}")
equip_group = doc.getObject("{layer_name}_Equipment")

box = doc.addObject("Part::Box", "{obj_name}")
box.Width = {width_mm}
box.Length = {length_mm}
box.Height = {height_mm}
box.Label = "{struct_id}"
box.Placement = FreeCAD.Placement(FreeCAD.Vector({corner_x}, {corner_y}, 0), FreeCAD.Rotation())
equip_group.addObject(box)
doc.recompute()
'''
                    freecad.execute_code(equip_code)

                # Set layer visibility
                visibility_code = f'''
import FreeCAD

doc = FreeCAD.getDocument("{doc_name}")
layer = doc.getObject("{layer_name}")
if hasattr(layer, "ViewObject") and layer.ViewObject:
    layer.ViewObject.Visibility = {is_active}
doc.recompute()
'''
                freecad.execute_code(visibility_code)

                created_layers.append({
                    "layer_name": layer_name,
                    "solution_id": sol_id,
                    "rank": rank,
                    "equipment_count": len(structures),
                    "visible": is_active
                })

            # Set view
            view_code = f'''
import FreeCAD
import FreeCADGui

doc = FreeCAD.getDocument("{doc_name}")
doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.viewTop()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
'''
            freecad.execute_code(view_code)

            # Build summary
            summary = [f"Created {len(created_layers)} solution layers in '{doc_name}':"]
            for layer_info in created_layers:
                visibility = "VISIBLE" if layer_info["visible"] else "hidden"
                summary.append(f"  - {layer_info['layer_name']} (Rank {layer_info['rank']}) [{visibility}]")
                summary.append(f"    Equipment: {layer_info['equipment_count']}")

            summary.append("\nUse set_layout_visibility() to toggle between solutions.")

            screenshot = freecad.get_active_screenshot()
            response = [TextContent(type="text", text="\n".join(summary))]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to import solutions as layers: {str(e)}")
            return [TextContent(type="text", text=f"Failed to import solutions: {str(e)}")]

    @mcp.tool()
    async def set_layout_visibility(
        doc_name: str,
        visible_layer: str | None = None,
        show_all: bool = False,
        include_screenshot: bool = False,
        ctx: Context = None,
    ) -> list[TextContent | ImageContent]:
        """Toggle visibility of solution layers for comparison.

        Args:
            doc_name: Document name
            visible_layer: Layer name to show (hides others except Common)
            show_all: If True, show all solution layers for overlay comparison

        Examples:
            # Show only Layout_1:
            set_layout_visibility(doc_name="SitePlan", visible_layer="Layout_1_Rank1")

            # Show all layers overlaid:
            set_layout_visibility(doc_name="SitePlan", show_all=True)

            # Hide all solution layers (show only Common):
            set_layout_visibility(doc_name="SitePlan")
        """
        freecad = get_freecad_connection()

        try:
            toggle_code = f'''
import FreeCAD

doc = FreeCAD.getDocument("{doc_name}")
if not doc:
    raise ValueError("Document '{doc_name}' not found")

# Get all layer groups (exclude "Common")
layer_groups = [obj for obj in doc.Objects
                if obj.TypeId == "App::DocumentObjectGroup"
                and obj.Name.startswith("Layout_")]

visible_layer = "{visible_layer or ''}"
show_all = {show_all}

updated = []
for layer in layer_groups:
    if layer.ViewObject:
        if show_all:
            layer.ViewObject.Visibility = True
            updated.append(f"{{layer.Name}}: visible")
        elif visible_layer and layer.Name == visible_layer:
            layer.ViewObject.Visibility = True
            updated.append(f"{{layer.Name}}: visible")
        elif visible_layer:
            layer.ViewObject.Visibility = False
            updated.append(f"{{layer.Name}}: hidden")
        else:
            # No specific layer, hide all
            layer.ViewObject.Visibility = False
            updated.append(f"{{layer.Name}}: hidden")

doc.recompute()
print(__import__("json").dumps({{"layers": updated}}))
'''
            res = freecad.execute_code(toggle_code)

            if show_all:
                summary = f"All solution layers in '{doc_name}' are now visible for comparison."
            elif visible_layer:
                summary = f"Layer '{visible_layer}' is now visible. Other solution layers are hidden."
            else:
                summary = f"All solution layers in '{doc_name}' are now hidden. Only Common layer visible."

            screenshot = freecad.get_active_screenshot()
            response = [TextContent(type="text", text=summary)]
            return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to toggle visibility: {str(e)}")
            return [TextContent(type="text", text=f"Failed to toggle visibility: {str(e)}")]

    @mcp.tool()
    async def present_layout_options(
        doc_prefix: str,
        solutions: list[dict],
        site_boundary: list[list[float]] | None = None,
        use_single_document: bool = True,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
        ctx: Context = None,
    ) -> list[TextContent | ImageContent]:
        """Create FreeCAD layout options for human review.

        Enables human-in-the-loop review of multiple feasible layouts.
        By default, creates all solutions as toggleable layers in a single document
        for easy comparison. Set use_single_document=False for legacy multi-document
        behavior.

        Args:
            doc_prefix: Prefix for document names (e.g., "CBG_SitePlan")
            solutions: List of solution dicts from sitefit_list_solutions, each with:
                       solution_id, rank, metrics, placements, structures
            site_boundary: Optional site boundary [[x,y], ...] in meters
            use_single_document: If True (default), creates all solutions as layers
                       in a single document. If False, creates separate documents.
            ctx: MCP context

        Returns:
            List of created documents/layers with solution IDs and metrics

        Examples:
            ```json
            {
                "doc_prefix": "CBG_SitePlan",
                "solutions": [
                    {
                        "solution_id": "sol_001",
                        "rank": 1,
                        "metrics": {"compactness": 0.85},
                        "placements": [{"structure_id": "DIG-101", "x": 50, "y": 40}],
                        "structures": [{"id": "DIG-101", "type": "digester", "footprint": {"shape": "circle", "d": 20}}]
                    }
                ],
                "site_boundary": [[0,0], [150,0], [150,100], [0,100], [0,0]]
            }
            ```
        """
        # New default: use single document with layers for easier comparison
        if use_single_document:
            return await import_solutions_as_layers(
                doc_name=doc_prefix,
                solutions=solutions,
                site_boundary=site_boundary,
                active_layer_index=0,
                include_screenshot=include_screenshot,
                detail_level=detail_level,
                ctx=ctx,
            )

        # Legacy multi-document behavior
        freecad = get_freecad_connection()
        if not freecad.check_connection():
            return [TextContent(type="text", text="FreeCAD connection not available")]

        created_docs = []

        for i, sol in enumerate(solutions):
            sol_id = sol.get("solution_id", f"unknown_{i}")
            rank = sol.get("rank", i + 1)
            metrics = sol.get("metrics", {})
            placements = sol.get("placements", [])
            structures = sol.get("structures", [])

            doc_name = f"{doc_prefix}_Option{i+1}_Rank{rank}"

            # Create document
            create_doc_code = f'''
import FreeCAD

# Create new document
doc = FreeCAD.newDocument("{doc_name}")
FreeCAD.setActiveDocument("{doc_name}")
print("doc_created")
'''
            res = freecad.execute_code(create_doc_code)
            if "doc_created" not in res.get("message", ""):
                created_docs.append({
                    "doc_name": doc_name,
                    "error": "Failed to create document"
                })
                continue

            # Add site boundary if provided
            if site_boundary:
                points_mm = [[p[0] * M_TO_MM, p[1] * M_TO_MM] for p in site_boundary]
                boundary_code = f'''
import FreeCAD
import Draft

doc = FreeCAD.getDocument("{doc_name}")
points = [FreeCAD.Vector(p[0], p[1], 0) for p in {points_mm}]
wire = Draft.make_wire(points, closed=True, face=False)
wire.Label = "SiteBoundary"
doc.recompute()
print("boundary_ok")
'''
                freecad.execute_code(boundary_code)

            # Create equipment envelopes and apply placements
            for struct in structures:
                struct_id = struct.get("id", "Unknown")
                struct_type = struct.get("type", "equipment")
                footprint = struct.get("footprint", {})
                shape = footprint.get("shape", "rect")
                height = struct.get("height", 5.0)
                height_mm = height * M_TO_MM

                # Find placement for this structure (support both id formats)
                placement = next(
                    (p for p in placements if p.get("structure_id") == struct_id or p.get("id") == struct_id),
                    None
                )
                x_m = placement.get("x", 0) if placement else 0
                y_m = placement.get("y", 0) if placement else 0
                rotation_deg = placement.get("rotation_deg", 0) if placement else 0
                x_mm = x_m * M_TO_MM
                y_mm = y_m * M_TO_MM

                if shape == "circle":
                    diameter = footprint.get("d", 10.0)
                    radius_mm = (diameter / 2) * M_TO_MM
                    equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
cyl = doc.addObject("Part::Cylinder", "{struct_id}")
cyl.Radius = {radius_mm}
cyl.Height = {height_mm}
cyl.Label = "{struct_id}"
# Apply position and rotation
rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), {rotation_deg})
cyl.Placement = FreeCAD.Placement(FreeCAD.Vector({x_mm}, {y_mm}, 0), rotation)
cyl.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
cyl.EquipmentType = "{struct_type}"
cyl.addProperty("App::PropertyString", "EquipmentId", "ProcessEng", "Stable equipment ID")
cyl.EquipmentId = "{struct_id}"
doc.recompute()
print("equip_ok")
'''
                else:  # rectangle
                    orig_w = footprint.get("w", 10.0)   # Contract w = X dimension at 0°
                    orig_h = footprint.get("h", 10.0)   # Contract h = Y dimension at 0°

                    # Pre-swap dimensions for 90/270 rotation (no FreeCAD rotation needed)
                    if rotation_deg in (90, 270):
                        width, length = orig_h, orig_w
                    else:
                        width, length = orig_w, orig_h

                    width_mm = width * M_TO_MM
                    length_mm = length * M_TO_MM
                    # FreeCAD Part::Box: Width=X, Length=Y, Height=Z
                    # Dimensions are pre-swapped, so simple center-to-corner offset
                    half_x_mm = width_mm / 2.0
                    half_y_mm = length_mm / 2.0
                    corner_x = x_mm - half_x_mm
                    corner_y = y_mm - half_y_mm

                    equip_code = f'''
import FreeCAD
import Part

doc = FreeCAD.getDocument("{doc_name}")
box = doc.addObject("Part::Box", "{struct_id}")
box.Width = {width_mm}   # Pre-swapped X dimension
box.Length = {length_mm}  # Pre-swapped Y dimension
box.Height = {height_mm}
box.Label = "{struct_id}"

# Simple corner placement (dimensions pre-swapped, no rotation needed)
box.Placement = FreeCAD.Placement(FreeCAD.Vector({corner_x}, {corner_y}, 0), FreeCAD.Rotation())

box.addProperty("App::PropertyString", "EquipmentType", "ProcessEng", "Equipment type")
box.EquipmentType = "{struct_type}"
box.addProperty("App::PropertyString", "EquipmentId", "ProcessEng", "Stable equipment ID")
box.EquipmentId = "{struct_id}"
doc.recompute()
print("equip_ok")
'''
                freecad.execute_code(equip_code)

            # Set view
            view_code = f'''
import FreeCAD
import FreeCADGui

doc = FreeCAD.getDocument("{doc_name}")
doc.recompute()
FreeCADGui.ActiveDocument.ActiveView.viewTop()
FreeCADGui.ActiveDocument.ActiveView.fitAll()
print("view_ok")
'''
            freecad.execute_code(view_code)

            created_docs.append({
                "doc_name": doc_name,
                "solution_id": sol_id,
                "rank": rank,
                "metrics": metrics,
                "equipment_count": len(structures)
            })

        # Build summary
        summary = ["Created layout option documents:"]
        for doc_info in created_docs:
            if "error" in doc_info:
                summary.append(f"  - {doc_info['doc_name']}: ERROR - {doc_info['error']}")
            else:
                metrics_str = ", ".join(f"{k}: {v:.2f}" if isinstance(v, float) else f"{k}: {v}"
                                       for k, v in doc_info.get("metrics", {}).items())
                summary.append(f"  - {doc_info['doc_name']} (Rank {doc_info['rank']})")
                if metrics_str:
                    summary.append(f"    Metrics: {metrics_str}")

        summary.append("\nReview each document in FreeCAD and select preferred layout.")

        screenshot = freecad.get_active_screenshot()
        response = [TextContent(type="text", text="\n".join(summary))]
        return add_screenshot_if_available(response, screenshot, include_screenshot)

    @mcp.tool()
    async def finalize_selected_layout(
        doc_name: str,
        solution_id: str,
        project_name: str = "",
        drawing_number: str = "",
        generate_techdraw: bool = True,
        export_pdf_path: str | None = None,
        cleanup_other_options: bool = False,
        other_option_docs: list[str] | None = None,
        layer_name: str | None = None,
        delete_other_layers: bool = False,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
        ctx: Context = None,
    ) -> list[TextContent | ImageContent]:
        """Finalize the selected layout after human review.

        After reviewing multiple layout options with present_layout_options,
        call this to finalize the selected option and optionally clean up others.

        Supports both layer-based workflow (single document with layers) and
        legacy multi-document workflow.

        Args:
            doc_name: Name of the document
            solution_id: ID of the selected solution (for tracking)
            project_name: Project name for TechDraw title block
            drawing_number: Drawing number for TechDraw title block
            generate_techdraw: Generate 2D plan sheet (default: True)
            export_pdf_path: Optional path to export PDF
            cleanup_other_options: Remove other option documents (default: False)
            other_option_docs: List of other document names to close (if cleanup requested)
            layer_name: Layer group name to finalize (for layer-based workflow)
            delete_other_layers: Delete other layout layers (default: False, just hides them)
            ctx: MCP context

        Returns:
            Status message with finalization results
        """
        freecad = get_freecad_connection()
        if not freecad.check_connection():
            return [TextContent(type="text", text="FreeCAD connection not available")]

        results = {
            "doc_name": doc_name,
            "solution_id": solution_id,
            "techdraw_generated": False,
            "pdf_exported": False,
            "docs_closed": 0,
            "layers_hidden": 0,
            "layers_deleted": 0
        }

        # Verify document exists and activate it
        activate_code = f'''
import FreeCAD

doc = FreeCAD.getDocument("{doc_name}")
if doc:
    FreeCAD.setActiveDocument("{doc_name}")
    print("doc_activated")
else:
    print("doc_not_found")
'''
        res = freecad.execute_code(activate_code)
        if "doc_not_found" in res.get("message", ""):
            return [TextContent(type="text", text=f"Document '{doc_name}' not found")]

        # Handle layer-based workflow
        if layer_name:
            layer_code = f'''
import FreeCAD
import FreeCADGui

doc = FreeCAD.getDocument("{doc_name}")
layers_hidden = 0
layers_deleted = 0

# Find all Layout_* groups
layout_groups = [obj for obj in doc.Objects
                 if obj.TypeId == "App::DocumentObjectGroup"
                 and obj.Label.startswith("Layout_")]

for group in layout_groups:
    if group.Label == "{layer_name}":
        # Keep selected layer visible
        if hasattr(group, "ViewObject") and group.ViewObject:
            group.ViewObject.Visibility = True
    else:
        # Hide or delete other layers
        if {str(delete_other_layers).lower()}:
            # Delete the layer and its contents
            for child in group.Group:
                doc.removeObject(child.Name)
            doc.removeObject(group.Name)
            layers_deleted += 1
        else:
            # Just hide
            if hasattr(group, "ViewObject") and group.ViewObject:
                group.ViewObject.Visibility = False
            layers_hidden += 1

doc.recompute()
print(f"layers_hidden={{layers_hidden}}")
print(f"layers_deleted={{layers_deleted}}")
'''
            layer_res = freecad.execute_code(layer_code)
            msg = layer_res.get("message", "")
            if "layers_hidden=" in msg:
                try:
                    results["layers_hidden"] = int(msg.split("layers_hidden=")[1].split()[0])
                except (IndexError, ValueError):
                    pass
            if "layers_deleted=" in msg:
                try:
                    results["layers_deleted"] = int(msg.split("layers_deleted=")[1].split()[0])
                except (IndexError, ValueError):
                    pass

        # Cleanup other option documents if requested (legacy multi-document workflow)
        if cleanup_other_options and other_option_docs:
            for other_doc in other_option_docs:
                if other_doc != doc_name:
                    close_code = f'''
import FreeCAD
try:
    FreeCAD.closeDocument("{other_doc}")
    print("closed_{other_doc}")
except:
    pass
'''
                    close_res = freecad.execute_code(close_code)
                    if f"closed_{other_doc}" in close_res.get("message", ""):
                        results["docs_closed"] += 1

        # Generate TechDraw if requested (delegate to existing tool)
        if generate_techdraw:
            techdraw_code = f'''
import FreeCAD
import TechDraw

doc = FreeCAD.getDocument("{doc_name}")

# Create TechDraw page
page = doc.addObject("TechDraw::DrawPage", "PlanSheet")

# Try to find template
template_paths = [
    "/usr/share/freecad/Mod/TechDraw/Templates/A1_Landscape_ISO7200_Pep.svg",
    "/usr/share/freecad-daily/Mod/TechDraw/Templates/A1_Landscape_ISO7200_Pep.svg",
]
template_path = None
import os
for path in template_paths:
    if os.path.exists(path):
        template_path = path
        break

if template_path:
    template_obj = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template_obj.Template = template_path
    page.Template = template_obj

# Collect source objects
source_objects = [obj for obj in doc.Objects
                  if not obj.TypeId.startswith("TechDraw::")
                  and hasattr(obj, "Shape") and obj.Shape]

if source_objects:
    # Create top view
    view = doc.addObject("TechDraw::DrawViewPart", "TopView")
    view.Source = source_objects
    view.Direction = FreeCAD.Vector(0, 0, -1)
    view.XDirection = FreeCAD.Vector(1, 0, 0)
    view.ScaleType = "Custom"
    view.Scale = 0.005  # 1:200
    page.addView(view)

    # Center view on page
    view.X = 400
    view.Y = 300

doc.recompute()
print("techdraw_ok")
'''
            td_res = freecad.execute_code(techdraw_code)
            results["techdraw_generated"] = "techdraw_ok" in td_res.get("message", "")

            # Export PDF if path provided
            if export_pdf_path and results["techdraw_generated"]:
                pdf_code = f'''
import FreeCAD
import TechDrawGui

doc = FreeCAD.getDocument("{doc_name}")
page = doc.getObject("PlanSheet")
if page:
    TechDrawGui.exportPageAsPdf(page, "{export_pdf_path}")
    print("pdf_exported")
'''
                pdf_res = freecad.execute_code(pdf_code)
                results["pdf_exported"] = "pdf_exported" in pdf_res.get("message", "")

        # Build summary
        summary = [f"Finalized layout: {doc_name}"]
        summary.append(f"  Solution ID: {solution_id}")
        if layer_name:
            summary.append(f"  Selected layer: {layer_name}")
        if results["layers_hidden"] > 0:
            summary.append(f"  Other layers hidden: {results['layers_hidden']}")
        if results["layers_deleted"] > 0:
            summary.append(f"  Other layers deleted: {results['layers_deleted']}")
        if results["techdraw_generated"]:
            summary.append("  TechDraw plan sheet: Generated")
        if results["pdf_exported"]:
            summary.append(f"  PDF exported: {export_pdf_path}")
        if results["docs_closed"] > 0:
            summary.append(f"  Other options closed: {results['docs_closed']}")

        screenshot = freecad.get_active_screenshot()
        response = [TextContent(type="text", text="\n".join(summary))]
        return add_screenshot_if_available(response, screenshot, include_screenshot)

    logger.info("Contract tools registered successfully")
