"""Response filtering utilities for reducing context consumption.

Provides compact vs full response modes for FreeCAD MCP tools.
Compact mode (default) returns only essential information.
Full mode includes all properties for debugging.

Note: Key names match FreeCAD serializer output (PascalCase).
See addon/FreeCADMCP/rpc_server/serialize.py for the source format.
"""

from typing import Any, Literal

# Type alias for detail level parameter
DetailLevel = Literal["compact", "full"]

# Essential top-level object fields to keep in compact mode
# These match the FreeCAD serializer output (PascalCase)
COMPACT_OBJECT_FIELDS = {"Name", "Label", "TypeId", "Placement", "Shape"}


def filter_object_properties(
    obj_data: dict[str, Any],
    detail_level: DetailLevel
) -> dict[str, Any]:
    """Filter object properties based on detail level.

    Args:
        obj_data: Full object dictionary from FreeCAD serializer
        detail_level: "compact" for essential fields, "full" for all fields

    Returns:
        Filtered object dictionary
    """
    if detail_level == "full":
        return obj_data

    # Keep only essential fields in compact mode
    # Keys match FreeCAD serializer: Name, Label, TypeId, Placement, Shape
    result = {}
    for key in COMPACT_OBJECT_FIELDS:
        if key in obj_data:
            result[key] = obj_data[key]

    # Always include error/success if present (for response handling)
    for key in ("success", "error", "message"):
        if key in obj_data:
            result[key] = obj_data[key]

    return result


def filter_objects_list(
    objects: list[dict[str, Any]],
    detail_level: DetailLevel
) -> list[dict[str, Any]]:
    """Filter a list of objects based on detail level.

    Args:
        objects: List of object dictionaries from FreeCAD serializer
        detail_level: "compact" for essential fields, "full" for all fields

    Returns:
        Filtered list of objects
    """
    if detail_level == "full":
        return objects

    # Keep only Name, Label, TypeId for list responses
    # Keys match FreeCAD serializer (PascalCase)
    return [
        {
            "Name": obj.get("Name"),
            "Label": obj.get("Label"),
            "TypeId": obj.get("TypeId"),
        }
        for obj in objects
    ]


def filter_contract_response(
    response: dict[str, Any],
    detail_level: DetailLevel
) -> dict[str, Any]:
    """Filter contract-related responses.

    Note: Contract data is always kept complete since it's needed for FreeCAD.
    Only metadata and debug info is filtered in compact mode.

    Args:
        response: Response dictionary
        detail_level: "compact" omits metadata, "full" includes everything

    Returns:
        Filtered response dictionary
    """
    if detail_level == "full":
        return response

    # Create filtered copy, omitting verbose metadata
    result = {}
    for key, value in response.items():
        if key in ("metadata", "debug_info", "timing"):
            continue  # Skip in compact mode
        result[key] = value

    return result
