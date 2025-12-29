"""TechDraw tools for generating 2D engineering plan sheets.

This module provides MCP tools for creating TechDraw plan sheets from
3D FreeCAD models, with proper title blocks, scales, and export to PDF/DXF.
"""

import logging
from datetime import datetime
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import TextContent, ImageContent

from .path_utils import wsl_to_windows_path
from .response_filters import DetailLevel

logger = logging.getLogger("FreeCADMCPserver.techdraw")

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

# Collect visible objects with shapes for the view
source_objects = []
for obj in doc.Objects:
    # Skip TechDraw objects, templates, and invisible objects
    if obj.TypeId.startswith("TechDraw::"):
        continue
    if hasattr(obj, "ViewObject") and hasattr(obj.ViewObject, "Visibility"):
        if not obj.ViewObject.Visibility:
            continue
    # Check if object has a Shape (Part objects, Draft Wires, etc.)
    if hasattr(obj, "Shape") and obj.Shape:
        source_objects.append(obj)

if not source_objects:
    raise ValueError("No visible objects with shapes found in document")

# Create top view (looking down Z axis)
view = doc.addObject("TechDraw::DrawViewPart", "{view_name}")
view.Source = source_objects
view.Direction = FreeCAD.Vector(0, 0, -1)  # Top view (looking down)
view.XDirection = FreeCAD.Vector(1, 0, 0)  # X points right

# Set scale
view.ScaleType = "Custom"
view.Scale = {scale_value}

# Add view to page
page.addView(view)

# Center the view on the page
# Get page size from template
page_width = page.Template.Width.Value if hasattr(page, "Template") and page.Template else {TEMPLATE_SIZES[template]["width"]}
page_height = page.Template.Height.Value if hasattr(page, "Template") and page.Template else {TEMPLATE_SIZES[template]["height"]}

# Position view in center of drawing area (accounting for title block margin ~50mm on right/bottom)
view.X = (page_width - 50) / 2
view.Y = (page_height - 50) / 2

# Recompute to update view
doc.recompute()

# Add equipment labels if requested
labels_added = 0
'''

        if include_labels:
            code += '''
# Add labels for equipment using DrawViewBalloon with leader lines
import math

try:
    # First calculate model center (which is what the view is centered on)
    all_bounds = [o.Shape.BoundBox for o in source_objects if hasattr(o, "Shape") and o.Shape]
    if all_bounds:
        model_min_x = min(b.XMin for b in all_bounds)
        model_max_x = max(b.XMax for b in all_bounds)
        model_min_y = min(b.YMin for b in all_bounds)
        model_max_y = max(b.YMax for b in all_bounds)
        model_center_x = (model_min_x + model_max_x) / 2
        model_center_y = (model_min_y + model_max_y) / 2
    else:
        model_center_x = 0.0
        model_center_y = 0.0

    view_x = float(view.X)
    view_y = float(view.Y)
    view_scale = float(view.Scale)

    # Check if DrawViewBalloon is available
    use_balloons = True
    try:
        test_balloon = doc.addObject("TechDraw::DrawViewBalloon", "TestBalloon")
        doc.removeObject("TestBalloon")
    except Exception:
        use_balloons = False

    for i, obj in enumerate(source_objects):
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

            # Balloon bubble position - spread in fan pattern with page bounds clamping
            angle = math.radians(60 + (i * 25) % 120)  # Spread 60-180 degrees (upper half)
            label_distance = 20.0  # mm from object center

            bubble_x = origin_x + label_distance * math.cos(angle)
            bubble_y = origin_y + label_distance * math.sin(angle)

            # Clamp to page bounds (with margins)
            margin = 15.0  # mm from edge
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
            logger.error(f"Failed to create TechDraw page: {e}")
            return [TextContent(type="text", text=f"Failed to create TechDraw page: {e}")]

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

doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")

page = doc.getObject("{page_name}")
if page is None:
    raise ValueError("TechDraw page '{page_name}' not found")

if not page.TypeId.startswith("TechDraw::DrawPage"):
    raise ValueError("Object '{page_name}' is not a TechDraw page")

export_results = []

pdf_path = "{export_pdf_path or ''}"
dxf_path = "{export_dxf_path or ''}"
svg_path = "{export_svg_path or ''}"

if pdf_path:
    try:
        parent_dir = os.path.dirname(pdf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        import TechDrawGui
        TechDrawGui.exportPageAsPdf(page, pdf_path)
        if os.path.exists(pdf_path):
            export_results.append(f"PDF: {{pdf_path}} ({{os.path.getsize(pdf_path)}} bytes)")
        else:
            export_results.append(f"PDF failed: file not created at {{pdf_path}}")
    except Exception as e:
        export_results.append(f"PDF failed: {{e}}")

if dxf_path:
    try:
        parent_dir = os.path.dirname(dxf_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        TechDraw.writeDXFPage(page, dxf_path)
        if os.path.exists(dxf_path):
            export_results.append(f"DXF: {{dxf_path}} ({{os.path.getsize(dxf_path)}} bytes)")
        else:
            export_results.append(f"DXF failed: file not created at {{dxf_path}}")
    except Exception as e:
        export_results.append(f"DXF failed: {{e}}")

if svg_path:
    try:
        parent_dir = os.path.dirname(svg_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        TechDraw.writeSVGPage(page, svg_path)
        if os.path.exists(svg_path):
            export_results.append(f"SVG: {{svg_path}} ({{os.path.getsize(svg_path)}} bytes)")
        else:
            export_results.append(f"SVG failed: file not created at {{svg_path}}")
    except Exception as e:
        export_results.append(f"SVG failed: {{e}}")

print("Exports: " + "; ".join(export_results))
'''

        try:
            res = freecad.execute_code(code)
            screenshot = freecad.get_active_screenshot()

            if res.get("success"):
                message = res.get("message", "Export completed")
                response = [TextContent(type="text", text=message)]
                return add_screenshot_if_available(response, screenshot, include_screenshot)
            else:
                error = res.get("error", "Unknown error")
                response = [TextContent(type="text", text=f"Export failed: {error}")]
                return add_screenshot_if_available(response, screenshot, include_screenshot)

        except Exception as e:
            logger.error(f"Failed to export TechDraw page: {e}")
            return [TextContent(type="text", text=f"Failed to export TechDraw page: {e}")]

    logger.info("TechDraw tools registered")
