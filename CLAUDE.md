# FreeCAD MCP Server (Process Engineering Extended)

Fork of neka-nat/freecad-mcp with process engineering extensions for Spatial Contract interchange.

## WSL + Windows Setup

This setup runs the MCP server in WSL while FreeCAD runs on Windows.

```
┌─────────────────────────────────────────────────────────────────┐
│                         WINDOWS                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  FreeCAD + MCP Addon                                     │   │
│  │  - RPC Server binds to 0.0.0.0:9875                     │   │
│  │  - Accepts connections from WSL                          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            ▲                                     │
│                            │ XML-RPC (port 9875)                │
└────────────────────────────┼────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                         WSL2                                     │
│                            │                                     │
│  ┌─────────────────────────▼───────────────────────────────┐   │
│  │  freecad-mcp (MCP Server)                                │   │
│  │  - Auto-detects Windows host IP                          │   │
│  │  - Connects to Windows FreeCAD                           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FREECAD_HOST` | Auto-detect | Windows host IP (override if auto-detect fails) |
| `FREECAD_PORT` | `9875` | FreeCAD RPC server port |

### Windows Firewall

You may need to allow incoming connections on port 9875:
```powershell
# Run in PowerShell as Administrator
New-NetFirewallRule -DisplayName "FreeCAD MCP RPC" -Direction Inbound -Port 9875 -Protocol TCP -Action Allow
```

## Architecture Role

FreeCAD MCP serves as the **Engineering Truth** layer in the architecture:

```
FreeCAD MCP (Engineering Truth)
    │
    ├── export_contract_json() ──────► site-fit (Constraint Solver)
    │                                        │
    │                                   placements[]
    │                                        │
    ├── apply_placements() ◄─────────────────┘
    │
    └── export_glb() ────────────────► Blender MCP (Visualization)
```

## Process Engineering Extensions

### New MCP Tools

| Tool | Purpose |
|------|---------|
| `export_contract_json` | Export site boundary + equipment envelopes → Spatial Contract JSON |
| `apply_placements` | Import solved positions from site-fit → update FreeCAD placements |
| `export_glb` | Export mesh for Blender visualization (converts to OBJ, needs GLB conversion) |
| `create_equipment_envelope` | Create simple equipment placeholder shapes (cylinder/box) |
| `create_site_boundary` | Create Draft Wire for site boundary polygon |
| `import_sitefit_contract` | Import complete site-fit contract (boundary, equipment, placements, roads) |
| `present_layout_options` | Create separate documents for each layout solution for review |
| `finalize_selected_layout` | Finalize selected layout with TechDraw output |
| `create_techdraw_plan_sheet` | Generate 2D engineering drawing with top view and title block |
| `export_techdraw_page` | Export TechDraw page to PDF/DXF/SVG |
| `list_techdraw_templates` | List available TechDraw template sizes |

### Response Parameters

All tools support these optional parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `include_screenshot` | `False` | Include viewport screenshot in response (increases context) |
| `detail_level` | `"compact"` | Response detail: `"compact"` for essential fields, `"full"` for all properties |

**Compact mode** returns only essential object fields: `Name`, `Label`, `TypeId`, `Placement`, `Shape`
**Full mode** returns all FreeCAD object properties for debugging

### Spatial Contract Schema

All contract JSON files follow the schema at `/home/hvksh/processeng/schemas/spatial_contract.json`.

Key points:
- **Unit is meters** - FreeCAD mm converted internally
- `equipment[]` populated by FreeCAD export (NO placements)
- `placements[]` populated by site-fit solver
- `equipment.id` must be stable across all layers

### Unit Handling

FreeCAD uses mm internally. The contract tools automatically convert:
- **Export**: mm → m (divide by 1000)
- **Import**: m → mm (multiply by 1000)

## Installation

### 1. Install FreeCAD Addon (Windows)

Copy the addon to FreeCAD's Mod directory on Windows:

**Option A: Manual copy**
```powershell
# In PowerShell, copy from WSL path to Windows FreeCAD Mod directory
Copy-Item -Recurse "\\wsl$\Ubuntu\home\hvksh\processeng\freecad-mcp\addon\FreeCADMCP" "$env:APPDATA\FreeCAD\Mod\"
```

**Option B: Symlink (requires Developer Mode)**
```powershell
# Enable Developer Mode first in Windows Settings > Privacy & Security > For developers
New-Item -ItemType SymbolicLink -Path "$env:APPDATA\FreeCAD\Mod\FreeCADMCP" -Target "\\wsl$\Ubuntu\home\hvksh\processeng\freecad-mcp\addon\FreeCADMCP"
```

**Option C: From WSL (using Windows path)**
```bash
# From WSL, copy to Windows AppData
cp -r /home/hvksh/processeng/freecad-mcp/addon/FreeCADMCP /mnt/c/Users/$(cmd.exe /c echo %USERNAME% 2>/dev/null | tr -d '\r')/AppData/Roaming/FreeCAD/Mod/
```

### 2. Start FreeCAD (Windows)

1. Open FreeCAD on Windows
2. Go to View > Workbenches > MCP Addon
3. Click 'FreeCAD MCP > Start RPC Server'

The RPC server binds to `0.0.0.0:9875` (accepts WSL connections).

### 3. Verify MCP Connection

The server auto-connects on startup. If FreeCAD isn't running, tools will error with connection failure.

## Original Tools (from neka-nat/freecad-mcp)

| Tool | Purpose |
|------|---------|
| `create_document` | Create new FreeCAD document |
| `create_object` | Create Part/Draft/PartDesign objects |
| `edit_object` | Modify object properties |
| `delete_object` | Remove object |
| `execute_code` | Run arbitrary Python code in FreeCAD |
| `get_view` | Get screenshot of view (Isometric, Front, Top, etc.) |
| `get_objects` | List all objects in document |
| `get_object` | Get single object properties |
| `insert_part_from_library` | Insert from parts library addon |
| `get_parts_list` | List available parts |

## Usage Examples

### Create Site Layout Model

```python
# 1. Create document
create_document(name="WWT_Plant")

# 2. Create site boundary (100m x 80m)
create_site_boundary(
    doc_name="WWT_Plant",
    boundary_points=[[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]]
)

# 3. Create equipment envelopes
create_equipment_envelope(
    doc_name="WWT_Plant",
    equipment_id="TK-101",
    equipment_type="storage_tank",
    shape="circle",
    diameter=12.0,
    height=8.5
)

create_equipment_envelope(
    doc_name="WWT_Plant",
    equipment_id="BLDG-001",
    equipment_type="building",
    shape="rectangle",
    width=20.0,
    length=30.0,
    height=6.0
)

# 4. Export contract for site-fit
export_contract_json(
    doc_name="WWT_Plant",
    project_name="danone-india-etp",
    boundary_object="SiteBoundary",
    output_path="/tmp/contract.json"
)
```

### Apply Solved Placements

```python
# After site-fit produces placements
apply_placements(
    doc_name="WWT_Plant",
    contract_path="/tmp/solved_contract.json"
)
```

## Dependencies

- Python 3.12+
- mcp[cli] >= 1.12.2
- FreeCAD with MCP addon running

## File Structure

```
freecad-mcp/
├── src/freecad_mcp/
│   ├── __init__.py
│   ├── server.py            # Main MCP server + original tools
│   ├── contract_tools.py    # Site-fit integration tools
│   ├── techdraw_tools.py    # TechDraw plan sheet generation
│   ├── response_filters.py  # Detail level filtering utilities
│   └── path_utils.py        # WSL/Windows path translation
├── addon/FreeCADMCP/        # FreeCAD addon (symlink to Mod)
│   ├── Init.py
│   ├── InitGui.py
│   └── rpc_server/
│       └── rpc_server.py
├── install_addon.sh         # Addon installation script
├── pyproject.toml
└── CLAUDE.md               # This file
```

## Recent Updates

### v0.2.0 (2025-12-29)
- **Context Optimization**: Added `detail_level` parameter to all tools (default: `"compact"`)
- **Screenshot Control**: `include_screenshot` defaults to `False` to reduce context
- **TechDraw Tools**: New `create_techdraw_plan_sheet` and `export_techdraw_page` tools
- **Response Filters**: New `response_filters.py` module for field filtering
- **Import Tool**: `import_sitefit_contract` for complete site-fit integration
- **Layout Review**: `present_layout_options` and `finalize_selected_layout` for multi-solution review
