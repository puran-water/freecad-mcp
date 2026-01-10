"""TechDraw tools for generating 2D engineering plan sheets.

This module provides MCP tools for creating TechDraw plan sheets from
3D FreeCAD models, with proper title blocks, scales, and export to PDF/DXF.
"""

from datetime import datetime
from typing import Any, Callable

import structlog
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import TextContent, ImageContent

from .path_utils import wsl_to_windows_path
from .response_filters import DetailLevel

logger = structlog.get_logger("FreeCADMCPserver.techdraw")

# Standard template sizes (mm)
TEMPLATE_SIZES = {
    "ISO_A0_Landscape": {"width": 1189, "height": 841},
    "ISO_A0_Portrait": {"width": 841, "height": 1189},
    "ISO_A1_Landscape": {"width": 841, "height": 594},
    "ISO_A1_Portrait": {"width": 594, "height": 841},
    "ISO_A2_Landscape": {"width": 594, "height": 420},
    "ISO_A2_Portrait": {"width": 420, "height": 594},
    "ISO_A3_Landscape": {"width": 420, "height": 297},
    "ISO_A3_Portrait": {"width": 297, "height": 420},
    "ISO_A4_Landscape": {"width": 297, "height": 210},
    "ISO_A4_Portrait": {"width": 210, "height": 297},
    "ANSI_D_Landscape": {"width": 864, "height": 559},
    "ANSI_E_Landscape": {"width": 1118, "height": 864},
}


def parse_scale(scale_str: str) -> float:
    """Parse scale string like '1:200' into a float."""
    if ":" in scale_str:
        parts = scale_str.split(":")
        return float(parts[0]) / float(parts[1])
    return float(scale_str)


def register_techdraw_tools(
    mcp: FastMCP,
    get_freecad_connection: Callable,
    add_screenshot_if_available: Callable,
) -> None:
    """Register TechDraw tools with the MCP server.

    Args:
        mcp: FastMCP server instance
        get_freecad_connection: Function to get FreeCAD connection
        add_screenshot_if_available: Function to add screenshots to responses
    """

    @mcp.tool()
    def create_techdraw_plan_sheet(
        ctx: Context,
        doc_name: str,
        page_name: str = "A1_PLAN",
        template: str = "ISO_A1_Landscape",
        view_name: str = "TopView",
        scale: str = "1:200",
        project_name: str = "",
        drawing_number: str = "",
        revision: str = "A",
        include_labels: bool = True,
        export_pdf_path: str | None = None,
        export_dxf_path: str | None = None,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Create a TechDraw plan sheet from the current FreeCAD model.

        Generates a 2D engineering drawing with top view, title block,
        and optional equipment labels. Exports to PDF and/or DXF.

        Args:
            doc_name: Name of the FreeCAD document to create the drawing from
            page_name: Name for the TechDraw page object (default: "A1_PLAN")
            template: Drawing template size/orientation (default: "ISO_A1_Landscape")
                      Options: ISO_A0-A4_Landscape/Portrait, ANSI_D/E_Landscape
            view_name: Name for the top view object (default: "TopView")
            scale: Drawing scale as ratio string (default: "1:200")
            project_name: Project name for title block
            drawing_number: Drawing number for title block (e.g., "100-GA-001")
            revision: Revision letter/number for title block (default: "A")
            include_labels: Whether to add equipment ID labels (default: True)
            export_pdf_path: Optional path to export PDF file
            export_dxf_path: Optional path to export DXF file

        Returns:
            Status message and optional screenshot of the TechDraw page

        Examples:
            Create a basic plan sheet:
            ```json
            {
                "doc_name": "SitePlan",
                "scale": "1:200",
                "project_name": "Acme WWTP",
                "export_pdf_path": "/tmp/plan.pdf"
            }
            ```

            Create with custom template and exports:
            ```json
            {
                "doc_name": "SitePlan",
                "template": "ISO_A0_Landscape",
                "scale": "1:500",
                "project_name": "Large Industrial Site",
                "drawing_number": "100-GA-001",
                "revision": "B",
                "export_pdf_path": "/tmp/site_plan.pdf",
                "export_dxf_path": "/tmp/site_plan.dxf"
            }
            ```
        """
        freecad = get_freecad_connection()

        # Parse scale
        try:
            scale_value = parse_scale(scale)
        except (ValueError, ZeroDivisionError) as e:
            return [TextContent(type="text", text=f"Invalid scale format '{scale}': {e}")]

        # Validate template
        if template not in TEMPLATE_SIZES:
            valid_templates = ", ".join(TEMPLATE_SIZES.keys())
            return [TextContent(
                type="text",
                text=f"Invalid template '{template}'. Valid options: {valid_templates}"
            )]

        # Convert WSL paths to Windows paths for cross-platform compatibility
        # FreeCAD runs on Windows but MCP server may run in WSL
        if export_pdf_path:
            export_pdf_path = wsl_to_windows_path(export_pdf_path)
        if export_dxf_path:
            export_dxf_path = wsl_to_windows_path(export_dxf_path)

        # Build the FreeCAD Python code to execute
        current_date = datetime.now().strftime("%Y-%m-%d")

        code = f'''
import FreeCAD
import FreeCADGui

doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")

# Import TechDraw module
import TechDraw

# Check if page already exists, remove it
existing_page = doc.getObject("{page_name}")
if existing_page:
    doc.removeObject("{page_name}")

# Check if template already exists, remove it
existing_template = doc.getObject("{page_name}_Template")
if existing_template:
    doc.removeObject("{page_name}_Template")

# Check if view already exists, remove it
existing_view = doc.getObject("{view_name}")
if existing_view:
    doc.removeObject("{view_name}")

# Create the TechDraw page
page = doc.addObject("TechDraw::DrawPage", "{page_name}")

# Create and set template
# FreeCAD ships with templates in share/Mod/TechDraw/Templates/
import os
freecad_path = FreeCAD.getHomePath()

# Map template name to actual file
template_map = {{
    "ISO_A0_Landscape": "A0_Landscape_blank.svg",
    "ISO_A0_Portrait": "A0_Portrait_blank.svg",
    "ISO_A1_Landscape": "A1_Landscape_blank.svg",
    "ISO_A1_Portrait": "A1_Portrait_blank.svg",
    "ISO_A2_Landscape": "A2_Landscape_blank.svg",
    "ISO_A2_Portrait": "A2_Portrait_blank.svg",
    "ISO_A3_Landscape": "A3_Landscape_blank.svg",
    "ISO_A3_Portrait": "A3_Portrait_blank.svg",
    "ISO_A4_Landscape": "A4_Landscape_blank.svg",
    "ISO_A4_Portrait": "A4_Portrait_blank.svg",
    "ANSI_D_Landscape": "ANSI_D_Landscape.svg",
    "ANSI_E_Landscape": "ANSI_E_Landscape.svg",
}}

template_file = template_map.get("{template}", "A1_Landscape_blank.svg")
template_paths = [
    os.path.join(freecad_path, "share", "Mod", "TechDraw", "Templates", template_file),
    os.path.join(freecad_path, "Mod", "TechDraw", "Templates", template_file),
    os.path.join(freecad_path, "data", "Mod", "TechDraw", "Templates", template_file),
]

# Also check for templates with title blocks
title_block_templates = [
    os.path.join(freecad_path, "share", "Mod", "TechDraw", "Templates", template_file.replace("_blank", "")),
    os.path.join(freecad_path, "Mod", "TechDraw", "Templates", template_file.replace("_blank", "")),
]
template_paths = title_block_templates + template_paths

template_path = None
for path in template_paths:
    if os.path.exists(path):
        template_path = path
        break

if template_path is None:
    # Use a minimal fallback - create page without template
    print(f"Warning: Template file not found, using blank page")
else:
    template_obj = doc.addObject("TechDraw::DrawSVGTemplate", "{page_name}_Template")
    template_obj.Template = template_path
    page.Template = template_obj

    # Set editable text fields in title block if available
    try:
        if hasattr(template_obj, "EditableTexts"):
            texts = template_obj.EditableTexts
            # Common editable field names in FreeCAD templates
            field_mapping = {{
                "TITLE": "{project_name}",
                "FC:Title": "{project_name}",
                "DRAWING_TITLE": "{project_name}",
                "DWG_NO": "{drawing_number}",
                "FC:DrawingNumber": "{drawing_number}",
                "DRAWING_NUMBER": "{drawing_number}",
                "REV": "{revision}",
                "FC:Revision": "{revision}",
                "REVISION": "{revision}",
                "DATE": "{current_date}",
                "FC:Date": "{current_date}",
                "SCALE": "{scale}",
                "FC:Scale": "{scale}",
            }}
            for field, value in field_mapping.items():
                if field in texts and value:
                    texts[field] = value
            template_obj.EditableTexts = texts
    except Exception as e:
        print(f"Warning: Could not set title block fields: {{e}}")

# Collect visible objects with shapes for the view (including compound sub-objects)
def collect_objects_with_shapes(objects, collected=None):
    """Recursively collect objects with shapes, including compound sub-objects."""
    if collected is None:
        collected = []
    for obj in objects:
        # Skip TechDraw objects, templates, and invisible objects
        if obj.TypeId.startswith("TechDraw::"):
            continue
        if hasattr(obj, "ViewObject") and hasattr(obj.ViewObject, "Visibility"):
            if not obj.ViewObject.Visibility:
                continue
        # Check if object has a Shape
        if hasattr(obj, "Shape") and obj.Shape:
            collected.append(obj)
            # For compounds, also check linked objects
            if hasattr(obj, "Links"):
                collect_objects_with_shapes(obj.Links, collected)
            if hasattr(obj, "OutList"):
                for child in obj.OutList:
                    if child not in collected and hasattr(child, "Shape") and child.Shape:
                        collected.append(child)
    return collected

source_objects = collect_objects_with_shapes(doc.Objects)

if not source_objects:
    raise ValueError("No visible objects with shapes found in document")

# Calculate model bounds for proper view positioning
all_bounds = [o.Shape.BoundBox for o in source_objects if hasattr(o, "Shape") and o.Shape]
if all_bounds:
    model_min_x = min(b.XMin for b in all_bounds)
    model_max_x = max(b.XMax for b in all_bounds)
    model_min_y = min(b.YMin for b in all_bounds)
    model_max_y = max(b.YMax for b in all_bounds)
    model_width = model_max_x - model_min_x
    model_height = model_max_y - model_min_y
else:
    model_width = 1000  # Fallback
    model_height = 1000

# Create top view (looking down Z axis)
view = doc.addObject("TechDraw::DrawViewPart", "{view_name}")
view.Source = source_objects
view.Direction = FreeCAD.Vector(0, 0, -1)  # Top view (looking down)
view.XDirection = FreeCAD.Vector(1, 0, 0)  # X points right

# Get page size from template
page_width = page.Template.Width.Value if hasattr(page, "Template") and page.Template else {TEMPLATE_SIZES[template]["width"]}
page_height = page.Template.Height.Value if hasattr(page, "Template") and page.Template else {TEMPLATE_SIZES[template]["height"]}

# Calculate drawing area (accounting for title block margin ~50mm on right/bottom, ~10mm on left/top)
drawing_area_width = page_width - 60  # 10mm left + 50mm right margin
drawing_area_height = page_height - 60  # 10mm top + 50mm bottom margin
drawing_center_x = 10 + drawing_area_width / 2  # Offset from left margin
drawing_center_y = 50 + drawing_area_height / 2  # Offset from bottom margin

# Calculate auto-scale to fit model in drawing area (with 10% margin for labels)
requested_scale = {scale_value}
fit_scale_x = (drawing_area_width * 0.9) / model_width if model_width > 0 else requested_scale
fit_scale_y = (drawing_area_height * 0.9) / model_height if model_height > 0 else requested_scale
auto_fit_scale = min(fit_scale_x, fit_scale_y)

# Use requested scale if it fits, otherwise use auto-fit
if requested_scale <= auto_fit_scale:
    final_scale = requested_scale
else:
    final_scale = auto_fit_scale
    print(f"Warning: Requested scale {{requested_scale}} too large, using auto-fit scale {{auto_fit_scale:.6f}}")

view.ScaleType = "Custom"
view.Scale = final_scale

# Add view to page
page.addView(view)

# Center the view on the drawing area
view.X = drawing_center_x
view.Y = drawing_center_y

# Recompute to update view
doc.recompute()

# Add equipment labels if requested
labels_added = 0
'''

        if include_labels:
            code += '''
# Add labels for equipment using DrawViewBalloon with leader lines
# Uses zone-based label placement with collision avoidance
import math

try:
    # Model bounds already calculated above
    model_center_x = (model_min_x + model_max_x) / 2
    model_center_y = (model_min_y + model_max_y) / 2

    view_x = float(view.X)
    view_y = float(view.Y)
    view_scale = float(view.Scale)

    # Define label zones around page margins (for professional leader line routing)
    # Zones: left, right, top, bottom strips for label placement
    zone_margin = 40.0  # Distance from page edge for label zones
    zone_width = 60.0   # Width of label zones
    min_label_spacing = 12.0  # Minimum vertical/horizontal spacing between labels

    # Calculate view bounds on page
    scaled_model_width = model_width * view_scale
    scaled_model_height = model_height * view_scale
    view_left = view_x - scaled_model_width / 2
    view_right = view_x + scaled_model_width / 2
    view_bottom = view_y - scaled_model_height / 2
    view_top = view_y + scaled_model_height / 2

    # Label zones (x_min, x_max, y_min, y_max) for each side
    zones = {
        "left": (zone_margin, zone_margin + zone_width, view_bottom, view_top),
        "right": (page_width - zone_margin - zone_width, page_width - zone_margin, view_bottom, view_top),
        "top": (view_left, view_right, page_height - zone_margin - zone_width, page_height - zone_margin),
        "bottom": (view_left, view_right, zone_margin, zone_margin + zone_width),
    }

    # Track occupied label positions per zone for collision avoidance
    occupied = {"left": [], "right": [], "top": [], "bottom": []}

    # Check if DrawViewBalloon is available
    use_balloons = True
    try:
        test_balloon = doc.addObject("TechDraw::DrawViewBalloon", "TestBalloon")
        doc.removeObject("TestBalloon")
    except Exception:
        use_balloons = False

    # Collect equipment objects (skip boundary wires, groups, etc.)
    equipment_objects = []
    for obj in source_objects:
        # Filter to only equipment (cylinders, boxes with EquipmentId or EquipmentType)
        has_equip_prop = hasattr(obj, "EquipmentId") or hasattr(obj, "EquipmentType")
        is_part = obj.TypeId in ("Part::Cylinder", "Part::Box", "Part::Feature")
        if has_equip_prop or (is_part and not obj.Name.startswith("SiteBoundary")):
            equipment_objects.append(obj)

    for i, obj in enumerate(equipment_objects):
        # Create balloon/annotation for equipment ID
        obj_name = obj.Label if hasattr(obj, "Label") else obj.Name

        # Get object center in model space
        if hasattr(obj, "Shape") and obj.Shape:
            bbox = obj.Shape.BoundBox
            obj_center_x = (bbox.XMin + bbox.XMax) / 2
            obj_center_y = (bbox.YMin + bbox.YMax) / 2

            # Calculate offset from model center
            offset_x = obj_center_x - model_center_x
            offset_y = obj_center_y - model_center_y

            # Page coordinates of object center (balloon origin/tip)
            origin_x = view_x + (offset_x * view_scale)
            origin_y = view_y + (offset_y * view_scale)

            # Determine best zone based on object position relative to model center
            # Route leader lines away from model center for clarity
            dx = offset_x
            dy = offset_y

            # Choose zone opposite to object position (labels point outward)
            if abs(dx) > abs(dy):
                # Object is more horizontal - use left/right zones
                zone_name = "right" if dx < 0 else "left"
            else:
                # Object is more vertical - use top/bottom zones
                zone_name = "top" if dy < 0 else "bottom"

            # Get zone bounds
            z = zones[zone_name]

            # Find non-overlapping position in zone
            if zone_name in ("left", "right"):
                # Vertical arrangement
                bubble_x = (z[0] + z[1]) / 2
                # Start at object's Y position, then adjust for collisions
                target_y = origin_y
                for oy in occupied[zone_name]:
                    if abs(target_y - oy) < min_label_spacing:
                        target_y = oy + min_label_spacing
                target_y = max(z[2] + 5, min(target_y, z[3] - 5))
                bubble_y = target_y
                occupied[zone_name].append(bubble_y)
            else:
                # Horizontal arrangement
                bubble_y = (z[2] + z[3]) / 2
                # Start at object's X position, then adjust for collisions
                target_x = origin_x
                for ox in occupied[zone_name]:
                    if abs(target_x - ox) < min_label_spacing:
                        target_x = ox + min_label_spacing
                target_x = max(z[0] + 5, min(target_x, z[1] - 5))
                bubble_x = target_x
                occupied[zone_name].append(bubble_x)

            # Clamp to page bounds (safety)
            margin = 10.0
            bubble_x = max(margin, min(bubble_x, page_width - margin))
            bubble_y = max(margin, min(bubble_y, page_height - margin))

            if use_balloons:
                # Create balloon with leader line
                balloon = doc.addObject("TechDraw::DrawViewBalloon", f"Balloon_{obj.Name}")
                balloon.Text = obj_name
                balloon.SourceView = view
                balloon.OriginX = origin_x  # Leader tip (at object)
                balloon.OriginY = origin_y
                balloon.X = bubble_x  # Balloon bubble position
                balloon.Y = bubble_y
                if hasattr(balloon, "BubbleShape"):
                    balloon.BubbleShape = "Rectangle"  # Cleaner for equipment IDs
                if hasattr(balloon, "TextSize"):
                    balloon.TextSize = 3.0  # mm
                if hasattr(balloon, "KinkLength"):
                    balloon.KinkLength = 5.0  # Leader bend distance
                page.addView(balloon)
            else:
                # Fallback to annotation with spread positioning
                anno = doc.addObject("TechDraw::DrawViewAnnotation", f"Label_{obj.Name}")
                anno.Text = [obj_name]
                anno.TextSize = 3.0  # mm
                anno.X = bubble_x
                anno.Y = bubble_y
                page.addView(anno)

            labels_added += 1

except Exception as e:
    print(f"Warning: Could not add all labels: {e}")
'''

        code += f'''
doc.recompute()

# Export outputs
import os
export_results = []

pdf_path = "{export_pdf_path or ''}"
dxf_path = "{export_dxf_path or ''}"

if pdf_path:
    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(pdf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        import TechDrawGui
        TechDrawGui.exportPageAsPdf(page, pdf_path)

        # Verify file was actually created
        if os.path.exists(pdf_path):
            file_size = os.path.getsize(pdf_path)
            export_results.append(f"PDF exported to: {{pdf_path}} ({{file_size}} bytes)")
        else:
            export_results.append(f"PDF export failed: file not created at {{pdf_path}}")
    except Exception as e:
        export_results.append(f"PDF export failed: {{e}}")

if dxf_path:
    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(dxf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        TechDraw.writeDXFPage(page, dxf_path)

        # Verify file was actually created
        if os.path.exists(dxf_path):
            file_size = os.path.getsize(dxf_path)
            export_results.append(f"DXF exported to: {{dxf_path}} ({{file_size}} bytes)")
        else:
            export_results.append(f"DXF export failed: file not created at {{dxf_path}}")
    except Exception as e:
        export_results.append(f"DXF export failed: {{e}}")

# Build result message
result_msg = f"TechDraw page '{{page.Name}}' created with {{len(source_objects)}} objects"
if labels_added > 0:
    result_msg += f", {{labels_added}} labels"
if export_results:
    result_msg += ". " + "; ".join(export_results)

print(result_msg)
'''

        try:
            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot()

            if res.get("success"):
                message = res.get("message", "TechDraw page created")
                response = [TextContent(type="text", text=message)]
                return add_screenshot_if_available(response, screenshot, include_screenshot)
            else:
                error = res.get("error", "Unknown error")
                response = [TextContent(type="text", text=f"Failed to create TechDraw page: {error}")]
                return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error("create_techdraw_page_failed", error=str(e))
            return [TextContent(type="text", text=f"Failed to create TechDraw page: {e}")]

    @mcp.tool()
    def techdraw_preflight(
        ctx: Context,
        doc_name: str,
    ) -> list[TextContent]:
        """Check TechDraw readiness before export.

        Verifies GUI availability, Xvfb presence, and display settings.
        Provides recommendations if TechDraw export may fail.

        Args:
            doc_name: Name of the FreeCAD document to check

        Returns:
            Preflight check results with recommendations

        Examples:
            Check if TechDraw export will work:
            ```json
            {
                "doc_name": "SitePlan"
            }
            ```
        """
        import os
        import shutil

        freecad = get_freecad_connection()

        # Check local environment (MCP server side)
        xvfb_available = shutil.which("xvfb-run") is not None
        display_set = "DISPLAY" in os.environ
        display_value = os.environ.get("DISPLAY", "")

        # Check FreeCAD side via RPC
        code = '''
import FreeCAD
import sys
import os

result = {
    "freecad_version": ".".join(str(x) for x in FreeCAD.Version()[:3]),
    "gui_available": hasattr(FreeCAD, "Gui") and FreeCAD.GuiUp,
    "techdraw_module": False,
    "techdraw_gui_module": False,
    "display_env": os.environ.get("DISPLAY", ""),
    "platform": sys.platform,
    "visible_objects": 0,
    "template_search_paths": [],
}

# Check TechDraw module
try:
    import TechDraw
    result["techdraw_module"] = True
except ImportError:
    pass

# Check TechDrawGui module (requires GUI)
try:
    import TechDrawGui
    result["techdraw_gui_module"] = True
except ImportError:
    pass

# Check document and visible objects
doc = FreeCAD.getDocument("DOC_NAME")
if doc:
    result["visible_objects"] = len([obj for obj in doc.Objects
        if hasattr(obj, "ViewObject") and obj.ViewObject and obj.ViewObject.Visibility])

# Get template search paths
try:
    pref = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Files")
    template_dir = pref.GetString("TemplateDir", "")
    if template_dir:
        result["template_search_paths"].append(template_dir)

    # Add default resource path
    resource_dir = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Templates")
    if os.path.isdir(resource_dir):
        result["template_search_paths"].append(resource_dir)
except:
    pass

print(repr(result))
'''.replace("DOC_NAME", doc_name)

        try:
            res = freecad.execute_code(code)

            if res.get("success"):
                # Parse the result from FreeCAD
                output = res.get("message", "")
                try:
                    fc_info = eval(output)  # Safe: we control the code
                except:
                    fc_info = {}
            else:
                fc_info = {"error": res.get("error", "Unknown error")}

        except Exception as e:
            fc_info = {"error": str(e)}

        # Build preflight report
        gui_available = fc_info.get("gui_available", False)
        techdraw_gui = fc_info.get("techdraw_gui_module", False)
        can_export_pdf = gui_available and techdraw_gui

        recommendations = []
        if not gui_available:
            if xvfb_available:
                recommendations.append("Run FreeCAD with Xvfb: xvfb-run -a freecad ...")
            else:
                recommendations.append("Install Xvfb: apt install xvfb")
            recommendations.append("Or set QT_QPA_PLATFORM=offscreen before starting FreeCAD")
            recommendations.append("Or use sitefit_export_pack for guaranteed headless PDF output")

        if not techdraw_gui and gui_available:
            recommendations.append("TechDrawGui module not available - check FreeCAD installation")

        if not display_set and not fc_info.get("platform", "").startswith("win"):
            recommendations.append("DISPLAY environment variable not set")

        # Build response
        report = {
            "preflight_status": "ready" if can_export_pdf else "not_ready",
            "can_export_pdf": can_export_pdf,
            "gui_available": gui_available,
            "techdraw_module": fc_info.get("techdraw_module", False),
            "techdraw_gui_module": techdraw_gui,
            "xvfb_available": xvfb_available,
            "display_set": display_set,
            "display_value": display_value or fc_info.get("display_env", ""),
            "freecad_version": fc_info.get("freecad_version", "unknown"),
            "platform": fc_info.get("platform", "unknown"),
            "visible_objects": fc_info.get("visible_objects", 0),
            "template_search_paths": fc_info.get("template_search_paths", []),
            "recommendations": recommendations,
            "fallback_available": "sitefit_export_pack",
        }

        if "error" in fc_info:
            report["error"] = fc_info["error"]

        # Format as readable text
        lines = [
            "TechDraw Preflight Check",
            "=" * 40,
            f"Status: {report['preflight_status'].upper()}",
            f"Can export PDF: {report['can_export_pdf']}",
            "",
            "Environment:",
            f"  FreeCAD version: {report['freecad_version']}",
            f"  Platform: {report['platform']}",
            f"  GUI available: {report['gui_available']}",
            f"  TechDraw module: {report['techdraw_module']}",
            f"  TechDrawGui module: {report['techdraw_gui_module']}",
            f"  DISPLAY: {report['display_value'] or '(not set)'}",
            f"  Xvfb available: {report['xvfb_available']}",
            "",
            f"Document '{doc_name}':",
            f"  Visible objects: {report['visible_objects']}",
        ]

        if report["template_search_paths"]:
            lines.append("")
            lines.append("Template search paths:")
            for path in report["template_search_paths"]:
                lines.append(f"  - {path}")

        if recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for rec in recommendations:
                lines.append(f"  - {rec}")

        if "error" in report:
            lines.append("")
            lines.append(f"Error: {report['error']}")

        logger.info(
            "techdraw_preflight_complete",
            doc_name=doc_name,
            can_export_pdf=can_export_pdf,
            gui_available=gui_available,
        )

        return [TextContent(type="text", text="\n".join(lines))]

    @mcp.tool()
    def list_techdraw_templates(ctx: Context) -> list[TextContent]:
        """List available TechDraw template sizes and orientations.

        Returns:
            List of available templates with their dimensions
        """
        lines = ["Available TechDraw Templates:", ""]
        for name, size in TEMPLATE_SIZES.items():
            lines.append(f"  {name}: {size['width']}mm x {size['height']}mm")

        return [TextContent(type="text", text="\n".join(lines))]

    @mcp.tool()
    def export_techdraw_page(
        ctx: Context,
        doc_name: str,
        page_name: str,
        export_pdf_path: str | None = None,
        export_dxf_path: str | None = None,
        export_svg_path: str | None = None,
        include_screenshot: bool = False,
        detail_level: DetailLevel = "compact",
    ) -> list[TextContent | ImageContent]:
        """Export an existing TechDraw page to PDF, DXF, or SVG.

        Args:
            doc_name: Name of the FreeCAD document
            page_name: Name of the TechDraw page to export
            export_pdf_path: Optional path to export PDF file
            export_dxf_path: Optional path to export DXF file
            export_svg_path: Optional path to export SVG file

        Returns:
            Status message with export results
        """
        freecad = get_freecad_connection()

        if not any([export_pdf_path, export_dxf_path, export_svg_path]):
            return [TextContent(type="text", text="Error: At least one export path must be specified")]

        # Convert WSL paths to Windows paths for cross-platform compatibility
        if export_pdf_path:
            export_pdf_path = wsl_to_windows_path(export_pdf_path)
        if export_dxf_path:
            export_dxf_path = wsl_to_windows_path(export_dxf_path)
        if export_svg_path:
            export_svg_path = wsl_to_windows_path(export_svg_path)

        code = f'''
import FreeCAD
import TechDraw
import os
import sys

# Collect diagnostics
diagnostics = {{
    "freecad_version": ".".join(str(x) for x in FreeCAD.Version()[:3]),
    "gui_mode": hasattr(FreeCAD, "Gui") and FreeCAD.GuiUp,
    "platform": sys.platform,
    "display_env": os.environ.get("DISPLAY", ""),
}}

doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")

page = doc.getObject("{page_name}")
if page is None:
    raise ValueError("TechDraw page '{page_name}' not found")

if not page.TypeId.startswith("TechDraw::DrawPage"):
    raise ValueError("Object '{page_name}' is not a TechDraw page")

# Count views in page
diagnostics["views_in_page"] = len([v for v in page.Views if hasattr(v, 'TypeId')])
diagnostics["template_found"] = page.Template is not None

export_results = []
export_errors = []

pdf_path = "{export_pdf_path or ''}"
dxf_path = "{export_dxf_path or ''}"
svg_path = "{export_svg_path or ''}"

if pdf_path:
    try:
        parent_dir = os.path.dirname(pdf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        import TechDrawGui
        diagnostics["techdraw_gui_available"] = True
        TechDrawGui.exportPageAsPdf(page, pdf_path)
        if os.path.exists(pdf_path):
            export_results.append(f"PDF: {{pdf_path}} ({{os.path.getsize(pdf_path)}} bytes)")
        else:
            export_errors.append({{"format": "pdf", "error": "file not created", "path": pdf_path}})
    except ImportError as e:
        diagnostics["techdraw_gui_available"] = False
        export_errors.append({{
            "format": "pdf",
            "error": "TechDrawGui requires Qt GUI",
            "error_code": "TECHDRAW_NO_GUI",
            "details": str(e),
        }})
    except Exception as e:
        export_errors.append({{"format": "pdf", "error": str(e), "error_code": "EXPORT_FAILED"}})

if dxf_path:
    try:
        parent_dir = os.path.dirname(dxf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        TechDraw.writeDXFPage(page, dxf_path)
        if os.path.exists(dxf_path):
            export_results.append(f"DXF: {{dxf_path}} ({{os.path.getsize(dxf_path)}} bytes)")
        else:
            export_errors.append({{"format": "dxf", "error": "file not created", "path": dxf_path}})
    except Exception as e:
        export_errors.append({{"format": "dxf", "error": str(e), "error_code": "EXPORT_FAILED"}})

if svg_path:
    try:
        parent_dir = os.path.dirname(svg_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        TechDraw.writeSVGPage(page, svg_path)
        if os.path.exists(svg_path):
            export_results.append(f"SVG: {{svg_path}} ({{os.path.getsize(svg_path)}} bytes)")
        else:
            export_errors.append({{"format": "svg", "error": "file not created", "path": svg_path}})
    except Exception as e:
        export_errors.append({{"format": "svg", "error": str(e), "error_code": "EXPORT_FAILED"}})

# Build result
result = {{
    "success": len(export_errors) == 0,
    "exports": export_results,
    "errors": export_errors,
    "diagnostics": diagnostics,
}}

print(repr(result))
'''

        try:
            import os
            import shutil

            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot()

            if res.get("success"):
                output = res.get("message", "")
                try:
                    result = eval(output)
                except:
                    result = {"success": True, "exports": [output]}

                if result.get("success"):
                    exports = result.get("exports", [])
                    message = "Export completed:\n" + "\n".join(f"  - {e}" for e in exports)
                    response = [TextContent(type="text", text=message)]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
                else:
                    # Format structured error response
                    errors = result.get("errors", [])
                    diagnostics = result.get("diagnostics", {})

                    lines = ["TechDraw Export Failed", "=" * 40, ""]

                    for err in errors:
                        lines.append(f"Format: {err.get('format', 'unknown').upper()}")
                        lines.append(f"  Error: {err.get('error', 'Unknown error')}")
                        if err.get("error_code"):
                            lines.append(f"  Code: {err.get('error_code')}")
                        lines.append("")

                    lines.append("Diagnostics:")
                    for key, value in diagnostics.items():
                        lines.append(f"  {key}: {value}")

                    # Add recommendations
                    recommendations = []
                    if not diagnostics.get("gui_mode"):
                        xvfb_available = shutil.which("xvfb-run") is not None
                        if xvfb_available:
                            recommendations.append("Run FreeCAD with Xvfb: xvfb-run -a freecad ...")
                        else:
                            recommendations.append("Install Xvfb: apt install xvfb")
                        recommendations.append("Or set QT_QPA_PLATFORM=offscreen")
                        recommendations.append("Or use sitefit_export_pack for guaranteed headless PDF output")

                    if recommendations:
                        lines.append("")
                        lines.append("Recommendations:")
                        for rec in recommendations:
                            lines.append(f"  - {rec}")

                    lines.append("")
                    lines.append("Fallback: Use sitefit_export_pack for headless-safe PDF generation")

                    logger.warning(
                        "techdraw_export_failed",
                        doc_name=doc_name,
                        page_name=page_name,
                        errors=errors,
                        diagnostics=diagnostics,
                    )

                    response = [TextContent(type="text", text="\n".join(lines))]
                    return add_screenshot_if_available(response, screenshot, include_screenshot)
            else:
                error = res.get("error", "Unknown error")
                logger.error(
                    "techdraw_export_rpc_failed",
                    doc_name=doc_name,
                    page_name=page_name,
                    error=error,
                )
                response = [TextContent(type="text", text=f"Export failed: {error}")]
                return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error("export_techdraw_page_failed", doc_name=doc_name, page_name=page_name, error=str(e))
            return [TextContent(type="text", text=f"Failed to export TechDraw page: {e}")]

    logger.info("techdraw_tools_registered")
