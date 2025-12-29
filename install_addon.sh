#!/bin/bash
# Install FreeCAD MCP addon
# This script creates a symlink from FreeCAD's Mod directory to the addon

set -e

ADDON_SOURCE="$(cd "$(dirname "$0")/addon/FreeCADMCP" && pwd)"
FREECAD_MOD_DIR="${HOME}/.local/share/FreeCAD/Mod"

echo "Installing FreeCAD MCP addon..."
echo "Source: ${ADDON_SOURCE}"
echo "Target: ${FREECAD_MOD_DIR}/FreeCADMCP"

# Create Mod directory if it doesn't exist
mkdir -p "${FREECAD_MOD_DIR}"

# Remove existing symlink or directory
if [ -L "${FREECAD_MOD_DIR}/FreeCADMCP" ]; then
    echo "Removing existing symlink..."
    rm "${FREECAD_MOD_DIR}/FreeCADMCP"
elif [ -d "${FREECAD_MOD_DIR}/FreeCADMCP" ]; then
    echo "Warning: Found existing FreeCADMCP directory, backing up..."
    mv "${FREECAD_MOD_DIR}/FreeCADMCP" "${FREECAD_MOD_DIR}/FreeCADMCP.bak.$(date +%Y%m%d%H%M%S)"
fi

# Create symlink
ln -s "${ADDON_SOURCE}" "${FREECAD_MOD_DIR}/FreeCADMCP"

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Start FreeCAD"
echo "2. Go to View > Workbenches > MCP Addon"
echo "3. Click 'FreeCAD MCP > Start RPC Server' to start the XML-RPC server on port 9875"
echo ""
echo "The MCP server can now connect to FreeCAD via XML-RPC."
