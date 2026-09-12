"""The design-system adapter (ADR-C4 Amendment A1).

Two properties matter here and neither is about geometry. First, the adapter
holds **no design logic** — every tool is a call into
``engineering_utils.cad``, so the shared modules stay the single authority.
Second, a check that cannot run must say so: a clash gate reporting nothing
because it never executed is indistinguishable, on a drawing, from one that
passed.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from freecad_mcp import design_tools

SOURCE = Path(design_tools.__file__).read_text()


# --- the adapter stays thin ---------------------------------------------


def test_the_adapter_imports_the_shared_modules_not_its_own_maths():
    """Design logic in the server would fork the authority in two."""
    tree = ast.parse(SOURCE)
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert any(m.startswith("engineering_utils.cad") for m in imported)


def test_no_tool_computes_geometry_itself():
    """No numeric literals doing work — the giveaway that logic leaked in."""
    banned = ("math.", "numpy", "np.")
    assert not any(token in SOURCE for token in banned)


def test_the_design_tools_are_registered():
    registered = []

    class _FakeMCP:
        def tool(self, **_annotations):
            def decorator(fn):
                registered.append(fn.__name__)
                return fn
            return decorator

    design_tools.register_design_tools(_FakeMCP(), lambda: None, lambda *a, **k: None)
    assert set(registered) == {
        "cad_basis_validate", "cad_edit_preview", "cad_edit_apply",
        "cad_clash_gate", "cad_publish",
        "cad_asset_inspect", "cad_recipe_export", "cad_review_export",
        # Reports what this PROCESS serves, which is the one question a stale
        # host cannot be asked any other way.
        "cad_capability_fingerprint",
    }


def test_every_registered_tool_documents_itself():
    captured = []

    class _FakeMCP:
        def tool(self, **_annotations):
            def decorator(fn):
                captured.append(fn)
                return fn
            return decorator

    design_tools.register_design_tools(_FakeMCP(), lambda: None, lambda *a, **k: None)
    for fn in captured:
        assert fn.__doc__ and len(fn.__doc__.strip()) > 40, fn.__name__


# --- a gate that cannot run must not look like a pass --------------------


def test_missing_kernel_reports_unavailable_not_clear(monkeypatch):
    monkeypatch.delenv(design_tools.KERNEL_PYTHON_ENV, raising=False)
    out = design_tools._clash_via_kernel("/nowhere/basis.yaml", "/nowhere/blocks")
    assert "UNAVAILABLE" in out
    assert "NOT a pass" in out


def test_a_kernel_path_that_does_not_exist_is_reported(monkeypatch):
    monkeypatch.setenv(design_tools.KERNEL_PYTHON_ENV, "/definitely/not/a/python")
    out = design_tools._clash_via_kernel("/nowhere/basis.yaml", "/nowhere/blocks")
    assert "UNAVAILABLE" in out


def test_a_failing_kernel_run_is_reported_as_failure(monkeypatch, tmp_path):
    """Exit code 1 must not be swallowed into silence."""
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv(design_tools.KERNEL_PYTHON_ENV, str(fake))
    out = design_tools._clash_via_kernel("/nowhere/basis.yaml", "/nowhere/blocks")
    assert "FAILED" in out and "NOT a pass" in out


def test_the_kernel_script_carries_the_paths_it_was_given(monkeypatch, tmp_path):
    """The delegated run must judge the basis actually asked for."""
    echo = tmp_path / "python"
    echo.write_text("#!/bin/sh\nprintf '%s' \"$2\"\n")
    echo.chmod(0o755)
    monkeypatch.setenv(design_tools.KERNEL_PYTHON_ENV, str(echo))
    out = design_tools._clash_via_kernel("/proj/cad_basis.yaml", "/lib/blocks")
    assert "/proj/cad_basis.yaml" in out and "/lib/blocks" in out


# --- loading -------------------------------------------------------------


def test_load_surfaces_a_missing_basis_rather_than_guessing():
    with pytest.raises(Exception):
        design_tools._load("/nowhere/cad_basis.yaml", "/nowhere/blocks")


def test_cad_worker_refuses_arbitrary_modules():
    with pytest.raises(ValueError, match="allow-listed"):
        design_tools._run_cad_module("subprocess", [])


def test_review_presentation_is_an_argv_value_not_code(monkeypatch):
    registered = {}
    class MCP:
        def tool(self, **_annotations):
            def capture(fn):
                registered[fn.__name__] = fn
                return fn
            return capture
    design_tools.register_design_tools(MCP(), lambda: None, lambda *a: None)
    captured = {}
    def call(module, args, *, backend="build123d"):
        captured.update(module=module, args=args, backend=backend)
        return "ok"
    monkeypatch.setattr(design_tools, "_run_cad_module", call)
    hostile = "a; this-is-not-a-command.json"
    registered["cad_review_export"](None,"basis","blocks","out","GA","Title","R2","2026-09-04",
                                    presentation_path=hostile)
    assert captured["args"][-2:] == ["--presentation", hostile]
    assert captured["backend"] == "build123d"


def test_cad_worker_passes_paths_as_argv_not_shell(monkeypatch, tmp_path):
    import subprocess
    fake = tmp_path / "python"
    fake.touch()
    monkeypatch.setenv(design_tools.KERNEL_PYTHON_ENV, str(fake))
    captured = {}
    def run(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
    monkeypatch.setattr(design_tools.subprocess, "run", run)
    hostile = "a'; touch /tmp/not-a-command; #.step"
    assert design_tools._run_cad_module("engineering_utils.cad.assets", ["inspect", "--step", hostile]) == "ok"
    assert captured["argv"][-1] == hostile
    assert not captured["kwargs"].get("shell")
