[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/neka-nat-freecad-mcp-badge.png)](https://mseep.ai/app/neka-nat-freecad-mcp)

# FreeCAD MCP

This repository is a FreeCAD MCP that allows you to control FreeCAD from Claude Desktop.

## Demo

### Design a flange

![demo](./assets/freecad_mcp4.gif)

### Design a toy car

![demo](./assets/make_toycar4.gif)

### Design a part from 2D drawing

#### Input 2D drawing

![input](./assets/b9-1.png)

#### Demo

![demo](./assets/from_2ddrawing.gif)

This is the conversation history.
https://claude.ai/share/7b48fd60-68ba-46fb-bb21-2fbb17399b48

## Install addon

FreeCAD Addon directory is
* Windows: `%APPDATA%\FreeCAD\Mod\`
* Mac: `~/Library/Application\ Support/FreeCAD/Mod/`
* Linux:
  * Ubuntu: `~/.FreeCAD/Mod/` or `~/snap/freecad/common/Mod/` (if you install FreeCAD from snap)
  * Debian: `~/.local/share/FreeCAD/Mod`

Please put `addon/FreeCADMCP` directory to the addon directory.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
cd freecad-mcp
cp -r addon/FreeCADMCP ~/.FreeCAD/Mod/
```

When you install addon, you need to restart FreeCAD.
You can select "MCP Addon" from Workbench list and use it.

![workbench_list](./assets/workbench_list.png)

And you can start RPC server by "Start RPC Server" command in "FreeCAD MCP" toolbar.

![start_rpc_server](./assets/start_rpc_server.png)

## Setting up Claude Desktop

Pre-installation of the [uvx](https://docs.astral.sh/uv/guides/tools/) is required.

And you need to edit Claude Desktop config file, `claude_desktop_config.json`.

For user.

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": [
        "freecad-mcp"
      ]
    }
  }
}
```

If you want to save token, you can set `only_text_feedback` to `true` and use only text feedback.

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": [
        "freecad-mcp",
        "--only-text-feedback"
      ]
    }
  }
}
```


For developer.
First, you need clone this repository.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
```

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/freecad-mcp/",
        "run",
        "freecad-mcp"
      ]
    }
  }
}
```

## Tools

* `create_document`: Create a new document in FreeCAD.
* `create_object`: Create a new object in FreeCAD.
* `edit_object`: Edit an object in FreeCAD.
* `delete_object`: Delete an object in FreeCAD.
* `execute_code`: Execute arbitrary Python code in FreeCAD.
* `insert_part_from_library`: Insert a part from the [parts library](https://github.com/FreeCAD/FreeCAD-library).
* `get_view`: Get a screenshot of the active view.
* `get_objects`: Get all objects in a document.
* `get_object`: Get an object in a document.
* `get_parts_list`: Get the list of parts in the [parts library](https://github.com/FreeCAD/FreeCAD-library).

---

## Process Engineering Extensions (puran-water fork)

This fork ([puran-water/freecad-mcp](https://github.com/puran-water/freecad-mcp)) adds process engineering extensions for wastewater/biogas facility design workflows.

### Architecture

FreeCAD MCP serves as the **Engineering Truth** layer in the process engineering toolchain:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SITE LAYOUT WORKFLOW                          │
│                                                                       │
│   site-fit MCP ──────► sitefit_export_contract()                     │
│   (Constraint Solver)          │                                      │
│                                ▼                                      │
│                    FreeCAD MCP ◄──────────────────────────────────   │
│                    (Engineering Truth)                                │
│                         │                                             │
│                         ├── import_sitefit_contract()                │
│                         ├── create_equipment_envelope()              │
│                         ├── create_techdraw_plan_sheet()             │
│                         │                                             │
│                         ▼                                             │
│                    Blender MCP (Visualization)                       │
│                         └── export_glb()                             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                           PFD WORKFLOW                               │
│                                                                       │
│   engineering-mcp ──────► dexpi_export_json()                        │
│   (SFILES/DEXPI)               │                                      │
│                                ▼                                      │
│                    FreeCAD MCP ◄──────────────────────────────────   │
│                    (CAD Generation)                                   │
│                         │                                             │
│                         ├── execute_code() → PIDWorkbench import     │
│                         ├── create_techdraw_plan_sheet()             │
│                         │                                             │
│                         ▼                                             │
│                    PDF / DXF / SVG Drawings                          │
└─────────────────────────────────────────────────────────────────────┘
```

### Extended Tools

#### Site Layout Tools (Contract-based)

| Tool | Purpose |
|------|---------|
| `import_sitefit_contract` | Import complete site-fit contract (boundary, equipment, placements, roads) |
| `export_contract_json` | Export site boundary + equipment envelopes as Spatial Contract JSON |
| `apply_placements` | Apply solved positions from site-fit back to FreeCAD objects |
| `create_equipment_envelope` | Create simple equipment placeholder shapes (cylinder/box) |
| `create_site_boundary` | Create Draft Wire for site boundary polygon |
| `present_layout_options` | Create separate documents for each layout solution for review |
| `finalize_selected_layout` | Finalize selected layout with TechDraw output |

#### TechDraw Tools

| Tool | Purpose |
|------|---------|
| `create_techdraw_plan_sheet` | Generate 2D engineering drawing with top view and title block |
| `export_techdraw_page` | Export TechDraw page to PDF/DXF/SVG |
| `list_techdraw_templates` | List available TechDraw template sizes (ISO A0-A4, ANSI D/E) |

### Response Optimization

All tools support optional parameters to reduce context consumption:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `include_screenshot` | `False` | Include viewport screenshot in response |
| `detail_level` | `"compact"` | `"compact"` for essential fields, `"full"` for all properties |

### Workflow Integration

#### Site Test Fit Workflow

Used by the `site-fit-workflow` skill for facility layout optimization:

```python
# 1. Generate layouts with site-fit MCP
result = sitefit_generate(site_boundary=..., structures=...)

# 2. Export contract for FreeCAD
contract = sitefit_export_contract(solution_id=result["solutions"][0]["id"])

# 3. Import into FreeCAD
import_sitefit_contract(doc_name="SitePlan", contract_json=contract)

# 4. Generate engineering drawing
create_techdraw_plan_sheet(
    doc_name="SitePlan",
    scale="1:200",
    project_name="WWTP Layout",
    export_pdf_path="/path/to/site_plan.pdf"
)
```

#### PFD Workflow

Used by the `pfd-skill` for Process Flow Diagram generation:

```python
# 1. Create PFD with engineering-mcp (SFILES tools)
pfd_id = sfiles_create_flowsheet(name="Aeration PFD", type="PFD")
# ... add equipment, streams, controls ...

# 2. Export to DEXPI JSON
dexpi_export(model_id=pfd_id, format="json", output_path="pfd.dexpi.json")

# 3. Import into FreeCAD via PIDWorkbench
execute_code("""
from PIDWorkbench.commands.import_cmd import DexpiImporter
DexpiImporter().import_dexpi("pfd.dexpi.json")
""")

# 4. Generate CAD drawing
create_techdraw_plan_sheet(
    doc_name="PFD",
    template="ISO_A3_Landscape",
    scale="1:50",
    project_name="WWTP Process",
    drawing_number="PFD-001",
    export_pdf_path="pfd_drawing.pdf"
)
```

### WSL + Windows Setup

This fork includes WSL support for running the MCP server in WSL while FreeCAD runs on Windows. See [CLAUDE.md](./CLAUDE.md) for detailed setup instructions.

### Installation (Fork)

```bash
# Clone the fork
git clone https://github.com/puran-water/freecad-mcp.git
cd freecad-mcp

# Install addon to FreeCAD (Windows)
./install_addon.sh

# Or manually copy
cp -r addon/FreeCADMCP "$APPDATA/FreeCAD/Mod/"
```

---

## Contributors

<a href="https://github.com/neka-nat/freecad-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=neka-nat/freecad-mcp" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
