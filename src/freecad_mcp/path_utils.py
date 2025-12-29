"""Path utilities for cross-platform WSL/Windows compatibility.

This module provides helpers for converting WSL paths to Windows paths
when FreeCAD runs on Windows but the MCP server runs in WSL.
"""

import os
import subprocess
from typing import Optional


def wsl_to_windows_path(wsl_path: str, use_forward_slashes: bool = True) -> str:
    """Convert WSL path to Windows path for cross-platform compatibility.

    This function handles the common case where the MCP server runs in WSL
    but FreeCAD runs on Windows. Paths like `/tmp/file.pdf` need to be
    converted to Windows-compatible paths like `C:/Users/.../Temp/file.pdf`.

    Args:
        wsl_path: Path to convert (WSL or Windows format)
        use_forward_slashes: If True, use forward slashes (safe for Python strings).
                            Windows accepts both forward and backslashes.

    Returns:
        Windows-compatible path, or original path if not in WSL or conversion fails.

    Examples:
        >>> wsl_to_windows_path("/tmp/plan.pdf")
        'C:/Users/user/AppData/Local/Temp/plan.pdf'

        >>> wsl_to_windows_path("C:/already/windows.pdf")
        'C:/already/windows.pdf'

        >>> wsl_to_windows_path("~/documents/file.pdf")
        'C:/Users/user/documents/file.pdf'
    """
    if not wsl_path:
        return wsl_path

    # Already a Windows path? Return as-is
    # Check for drive letter patterns: C:\, C:/, or UNC paths \\server\
    if len(wsl_path) >= 3:
        if wsl_path[1:3] == ':\\' or wsl_path[1:3] == ':/':
            return wsl_path
    if wsl_path.startswith('\\\\'):
        return wsl_path

    # Check if running in WSL
    is_wsl = os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
    if not is_wsl:
        return wsl_path

    # Expand ~ before conversion
    expanded_path = os.path.expanduser(wsl_path)

    # Use wslpath -m for forward slashes (safe in Python strings - no unicode escape issues)
    # Use wslpath -w for backslashes (Windows native, but risky in string literals)
    flag = '-m' if use_forward_slashes else '-w'

    try:
        result = subprocess.run(
            ['wslpath', flag, expanded_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fallback for common paths if wslpath fails
    if expanded_path.startswith('/tmp/'):
        try:
            # Get Windows %TEMP% directory
            win_temp_result = subprocess.run(
                ['cmd.exe', '/c', 'echo', '%TEMP%'],
                capture_output=True,
                text=True,
                timeout=5
            )
            win_temp = win_temp_result.stdout.strip()
            if win_temp and not win_temp.startswith('%'):
                rest = expanded_path[5:]  # Remove '/tmp/'
                if use_forward_slashes:
                    win_temp = win_temp.replace('\\', '/')
                    return f"{win_temp}/{rest}"
                else:
                    return f"{win_temp}\\{rest}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # Return unchanged if conversion fails
    return wsl_path


def is_wsl() -> bool:
    """Check if running in Windows Subsystem for Linux.

    Returns:
        True if running in WSL, False otherwise.
    """
    return os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")


def ensure_parent_dir(path: str) -> None:
    """Ensure the parent directory of a path exists.

    Creates the parent directory if it doesn't exist. Works with both
    WSL and Windows paths.

    Args:
        path: File path whose parent directory should be created.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
