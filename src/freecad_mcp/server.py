import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict
from uuid import uuid4

import structlog
from fastmcp import Context, FastMCP
from mcp.types import ImageContent, TextContent



def get_windows_host_ip() -> str:
    """Detect Windows host IP when running in WSL.

    Detection methods (in order):
    1. FREECAD_HOST environment variable (explicit override)
    2. WSL2 mirrored networking: Use localhost (shares Windows network stack)
    3. WSL2 NAT mode: Default gateway from ip route
    4. WSL2 NAT mode: Parse /etc/resolv.conf for nameserver (fallback)
    5. Fallback: localhost (native Windows or same-machine setup)

    Note: WSL2 mirrored networking mode allows localhost to reach Windows services
    directly. This is detected by checking /etc/wsl.conf or absence of WSL-specific
    gateway routes.
    """
    # 1. Explicit override via environment variable
    env_host = os.environ.get("FREECAD_HOST")
    if env_host:
        return env_host

    # 2. Check if we're in WSL by looking for WSL-specific files
    is_wsl = False
    try:
        is_wsl = os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
        if not is_wsl and os.path.exists("/proc/version"):
            with open("/proc/version", "r") as f:
                is_wsl = "microsoft" in f.read().lower()
    except (PermissionError, IOError):
        pass

    if not is_wsl:
        return "localhost"

    # 3. Check for WSL2 mirrored networking mode
    # In mirrored mode, localhost works directly to reach Windows services
    # Detect by checking if /etc/wsl.conf has networkingMode=mirrored
    # or by testing if localhost can reach Windows (simpler: check wsl.conf)
    try:
        with open("/etc/wsl.conf", "r") as f:
            content = f.read().lower()
            if "networkingmode" in content and "mirrored" in content:
                return "localhost"
    except (FileNotFoundError, PermissionError):
        pass

    # Also check Windows-side .wslconfig via environment or by testing connectivity
    # Simpler heuristic: if no default gateway exists, assume mirrored mode
    try:
        result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Output format: "default via 192.168.x.1 dev eth0"
            parts = result.stdout.split()
            if "via" in parts:
                idx = parts.index("via")
                if idx + 1 < len(parts):
                    gateway_ip = parts[idx + 1]
                    if gateway_ip and not gateway_ip.startswith("127."):
                        # Test if gateway is reachable on target port, otherwise try localhost
                        # For now, return gateway (NAT mode)
                        return gateway_ip
            else:
                # No gateway route - likely mirrored mode
                return "localhost"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 4. Fallback to /etc/resolv.conf nameserver (NAT mode)
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                if line.startswith("nameserver"):
                    ip = line.split()[1].strip()
                    if ip and not ip.startswith("127."):
                        return ip
    except (FileNotFoundError, IndexError, PermissionError):
        pass

    # 5. Fallback - try localhost (works in mirrored mode)
    return "localhost"


# Configure structured logging (JSON to stderr for MCP compatibility)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
)

logger = structlog.get_logger("plant-cad-mcp")


_only_text_feedback = False


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Startup no longer connects to anything.

    It used to open an XML-RPC channel to a running FreeCAD and warn when that
    failed. There is nothing to connect to: every tool on this server either
    computes in-process or delegates to the pinned build123d interpreter.
    """
    logger.info("server_starting")
    yield {}
    logger.info("server_stopped")


mcp = FastMCP(
    "plant-cad-mcp",
    instructions=("Process plant CAD through shared canonical contracts and headless build123d tooling; "
                  "FreeCAD is an optional authenticated verification adapter. "
                  "The browser review viewer is separate from the modeling and verification tools."),
    lifespan=server_lifespan,
)


# Helper function to safely add screenshot to response
def add_screenshot_if_available(response, screenshot, include_screenshot: bool = False):
    """Safely add screenshot to response only if requested and available.

    Args:
        response: List of content items to append to
        screenshot: Base64-encoded PNG screenshot data, or None if unavailable
        include_screenshot: If False (default), skip screenshot entirely.
                           If True, add screenshot when available.

    Returns:
        The response list (modified in place)
    """
    # Don't add screenshots unless explicitly requested
    if not include_screenshot or _only_text_feedback:
        return response

    if screenshot is not None:
        response.append(ImageContent(type="image", data=screenshot, mimeType="image/png"))
    # Note: We no longer add "preview unavailable" message in compact mode
    # Only show that message if explicitly requesting screenshots and they fail
    return response


# FREECAD_MCP_ENABLE_RAW_EXECUTION is gone with the interpreter it escaped to.
# It was a break-glass that handed raw Python to a live FreeCAD process; there
# is no such process now, and geometry is produced by typed adapters only.


# The four associative TechDraw verification operations are gone with FreeCAD.
# What they did, the native path does on the ISSUE path rather than beside it:
# a DimensionSpec carries a typed MeasurementExpression that ga_sheets
# re-evaluates against the current basis, so a dimension is bound to block
# placements and route waypoints rather than to projected 2D topology, and a
# sheet with no resolver reports UNEVALUATED instead of passing.

# Register clearance envelope + gate tools
from .clearance_tools import register_clearance_tools  # noqa: E402

register_clearance_tools(mcp, None, add_screenshot_if_available)

# Register the CAD design-system adapter (ADR-C4 Amendment A1): a thin surface
# over engineering_utils.cad — no design logic lives in this server.
from .design_tools import register_design_tools  # noqa: E402

register_design_tools(mcp, None, add_screenshot_if_available)

from .asset_tools import register_asset_tools  # noqa: E402

register_asset_tools(mcp)

from .resolution_tools import register_resolution_tools  # noqa: E402

register_resolution_tools(mcp)


def main():
    """Run the MCP server"""
    global _only_text_feedback
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--only-text-feedback", action="store_true", help="Only return text feedback")
    args = parser.parse_args()
    _only_text_feedback = args.only_text_feedback
    logger.info("server_config", only_text_feedback=_only_text_feedback)
    mcp.run()


if __name__ == "__main__":
    main()
