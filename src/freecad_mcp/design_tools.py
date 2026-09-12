"""Typed adapter surface for the CAD design system (ADR-C4 Amendment A1).

The rule the amendment sets is that **computation stays in the shared headless
modules** and this server exposes only the minimum surface needed to drive
them. So there is no design logic here: every tool loads a basis, calls
``engineering_utils.cad``, and formats the answer.

The original five design tools are joined by three bounded asset/review adapters:

* ``cad_basis_validate`` — what is wrong with the design right now
* ``cad_edit_preview``   — what *would* an edit break (never writes)
* ``cad_edit_apply``     — transactional apply, refusing to break by default
* ``cad_clash_gate``     — solid-vs-solid interference
* ``cad_publish``        — issue sheets, refusing on any open error
* ``cad_asset_inspect``  — physical STEP evidence, never vendor certification
* ``cad_recipe_export``  — sourced civil/GA recipe to neutral STEP
* ``cad_review_export``  — explicitly non-issuable model/drawing package

The clash gate needs a geometry kernel. This server's environment usually has
none (it drives a Windows GUI over XML-RPC), so the tool **delegates to a
headless FreeCAD interpreter** when one is configured and says plainly when it
cannot run — a clash check that silently does not happen is worse than one that
refuses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Literal

import structlog
from fastmcp import Context
from mcp.types import TextContent

logger = structlog.get_logger("FreeCADMCPserver.design")

#: Interpreter with FreeCAD importable, for the clash gate. Set to the conda
#: env used for headless work; unset means the gate reports UNAVAILABLE.
#: The one geometry interpreter. ``KERNEL_PYTHON_ENV`` is kept as the name the
#: rest of this module uses so a second backend, if one is ever qualified, has
#: an obvious place to be named -- it is no longer a choice between two.
BUILD123D_PYTHON_ENV = "PURANOS_BUILD123D_PYTHON"
KERNEL_PYTHON_ENV = BUILD123D_PYTHON_ENV


try:  # pragma: no cover - depends on the installed mcp
    from mcp.types import ToolAnnotations
except ImportError:  # older mcp in the geometry runtime
    ToolAnnotations = None


def _ann(**hints):
    """Tool annotations, when the installed mcp knows about them.

    The pinned build123d runtime imports this module only to read its envelope
    contract, never to serve tools, and carries an older mcp without
    ToolAnnotations. Annotations describe the SERVED surface, so losing them in
    an import that serves nothing costs nothing — breaking that import costs a
    cross-runtime contract test.
    """
    return ToolAnnotations(**hints) if ToolAnnotations is not None else None


def _text(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]


def _load(basis_path: str, blocks_root: str):
    from engineering_utils.cad.basis import load_basis, load_interfaces

    return load_basis(basis_path), load_interfaces(blocks_root)


def _clash_via_kernel(basis_path: str, blocks_root: str, *, backend: str = "build123d") -> str:
    """Run the shared gate in its isolated selected kernel interpreter."""

    if backend != "build123d":raise ValueError("unsupported CAD backend; build123d is the only one")
    variable=BUILD123D_PYTHON_ENV
    kernel = os.environ.get(variable)
    if not kernel or not Path(kernel).exists():
        return (
            "CLASH GATE UNAVAILABLE — no geometry kernel.\n"
            f"Set {variable} to a python with build123d importable. "
            "The gate is NOT a pass; it did not run."
        )

    script = (
        "import sys, os\n"
        "lib = None  # one kernel; nothing to put on the path beside it\n"
        "if lib: sys.path.insert(0, lib)\n"
        "from engineering_utils.cad.basis import load_basis, load_interfaces, basis_hash\n"
        "from engineering_utils.cad.geometry import interference_from_basis\n"
        "from engineering_utils.cad.publish import _compiled_lines\n"
        "from engineering_utils.cad.interference import format_report\n"
        f"b = load_basis({basis_path!r})\n"
        f"lib_ = load_interfaces({blocks_root!r})\n"
        f"run = interference_from_basis(b, lib_, model_hash=basis_hash(b), compiled_lines=_compiled_lines(b, lib_), backend={backend!r})\n"
        "print(format_report(run))\n"
    )
    # Point the kernel interpreter at whatever engineering_utils THIS process
    # resolved, rather than a path computed from __file__ — the latter is wrong
    # the moment the server is installed anywhere but the repo it was built in.
    import engineering_utils  # noqa: PLC0415

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    src = str(Path(engineering_utils.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src

    try:
        result = subprocess.run([kernel, "-c", script], capture_output=True, text=True,
                                timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return "CLASH GATE FAILED — the kernel run timed out. This is NOT a pass."
    if result.returncode != 0:
        return (f"CLASH GATE FAILED (exit {result.returncode}). This is NOT a pass.\n"
                f"{result.stderr[-2000:]}")
    return result.stdout.strip()


def _run_cad_module(module: str, arguments: list[str], *, backend: str = "build123d") -> str:
    """Fixed shared entrypoints and argv only; no shell or caller-supplied code."""
    if module not in {"engineering_utils.cad.assets", "engineering_utils.cad.recipes",
                      "engineering_utils.cad.review", "engineering_utils.cad.pattern_library",
                      "engineering_utils.cad.clearance"}:
        raise ValueError("CAD entrypoint is not allow-listed")
    if backend != "build123d":
        raise ValueError("CAD backend is not allow-listed")
    variable=BUILD123D_PYTHON_ENV
    kernel = os.environ.get(variable)
    if not kernel or not Path(kernel).is_file():
        return f"CAD WORKER UNAVAILABLE — configure {variable}; no artifact was produced."
    import engineering_utils
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    src = str(Path(engineering_utils.__file__).resolve().parents[1])
    # One kernel: nothing else may go on the path beside the OCP runtime.
    env["PYTHONPATH"] = src
    try:
        result = subprocess.run([kernel, "-m", module, *arguments], capture_output=True,
                                text=True, timeout=1800 if module == "engineering_utils.cad.review" else 900, env=env)
    except subprocess.TimeoutExpired:
        return "CAD WORKER FAILED — timeout; inspect the output directory before retrying."
    if result.returncode:
        return f"CAD WORKER FAILED ({result.returncode}) — no approval asserted.\n{result.stderr[-4000:]}"
    return result.stdout[-8000:].strip()


def _served_tool_names():
    """Names the running server actually exposes, not what the repo defines."""
    from freecad_mcp import server as _server
    import asyncio
    try:
        tools = asyncio.run(_server.mcp.list_tools())
    except RuntimeError:                                      # already in a loop
        return []
    return [t.name for t in tools]


def _worker_identity():
    """Ask the pinned geometry interpreter what it is. A different process.

    Reported as unavailable rather than guessed when the interpreter is not
    configured: a fingerprint that invents an answer is worse than one that
    says it cannot see.
    """
    import json
    import subprocess

    kernel = os.environ.get(BUILD123D_PYTHON_ENV)
    if not kernel or not Path(kernel).is_file():
        return {"available": False,
                "reason": f"{BUILD123D_PYTHON_ENV} is unset or not a file"}
    script = (
        "import json\n"
        "from engineering_utils.cad.build_recipe import build_recipe_hash, runtime_versions\n"
        "print(json.dumps({'build_recipe_hash': build_recipe_hash(),\n"
        "                  'runtime_versions': runtime_versions()}))\n"
    )
    try:
        import engineering_utils
        env = dict(os.environ,
                   PYTHONPATH=str(Path(engineering_utils.__file__).resolve().parents[1]))
        done = subprocess.run([kernel, "-c", script], capture_output=True,
                              text=True, timeout=120, env=env)
        if done.returncode != 0:
            return {"available": False, "reason": done.stderr.strip()[-400:]}
        return {"available": True, **json.loads(done.stdout)}
    except Exception as exc:                                  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:300]}


def register_design_tools(mcp, _unused_connection: Callable | None, add_screenshot: Callable) -> None:
    """Register the design-system adapter tools."""

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False,
                               idempotentHint=True))
    def cad_capability_fingerprint(ctx: Context) -> list[TextContent]:
        """What THIS SERVER PROCESS can do, as opposed to what the repo contains.

        Implemented capability is not activated capability. A host keeps the
        tool inventory it started with, so a session can be holding an older
        surface while newer code sits on disk — which has already happened
        here: a stale host served older layout tools and a catalog missing the
        thermoplastic request schema, and the mismatch was invisible from
        inside the session. Read this before any release workflow, and before
        believing a tool is absent.

        Two identities are reported separately on purpose. The SERVER half is
        this process: its repo revision and the tools it is actually serving.
        The WORKER half is the pinned geometry interpreter, which is a
        different process with a different environment — the server imports no
        geometry kernel at all, so asking it what build123d version it has
        would answer about the wrong Python.
        """
        import json
        import subprocess
        from pathlib import Path as _Path

        import engineering_utils

        repo = _Path(engineering_utils.__file__).resolve().parents[3]
        def _git(*args):
            try:
                return subprocess.run(["git", "-C", str(repo), *args],
                                      capture_output=True, text=True,
                                      timeout=10).stdout.strip() or None
            except Exception:                                 # noqa: BLE001
                return None

        served = sorted(getattr(t, "name", str(t)) for t in _served_tool_names())
        report = {
            "server": {
                "repo_revision": _git("rev-parse", "HEAD"),
                "repo_dirty": bool(_git("status", "--porcelain")),
                "tool_count": len(served),
                "tools": served,
                "note": ("this is the inventory THIS PROCESS serves; if it "
                         "disagrees with the repository, the host has not "
                         "reconnected since the code changed"),
            },
            "worker": _worker_identity(),
        }
        return _text(json.dumps(report, indent=2, sort_keys=True))

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def cad_asset_inspect(ctx: Context, step_path: str, report_path: str,
                          ) -> list[TextContent]:
        """Inspect a STEP's physical bodies, hierarchy, bounds and excluded datums.

        Uses the shared headless kernel. Writes a new inspection report only;
        does not certify vendor dimensions, nozzle registration or project selection.
        """
        return _text(_run_cad_module("engineering_utils.cad.assets",
                     ["inspect", "--step", step_path, "--out", report_path]))

    @mcp.tool(annotations=_ann(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
    def cad_recipe_export(ctx: Context, recipe_path: str, out_dir: str,
                          recipe_kind: Literal["primitives","equipment_pattern"] = "primitives") -> list[TextContent]:
        """Build neutral STEP and a block interface from a sourced civil/GA recipe.

        Accepts the strict shared recipe data contract, never executable Python.
        This creates coordination geometry, not approved vendor fabrication detail.
        """
        if recipe_kind=="equipment_pattern":
            return _text(_run_cad_module("engineering_utils.cad.pattern_library",
                ["--pattern",recipe_path,"--out",out_dir]))
        return _text(_run_cad_module("engineering_utils.cad.recipes",
                     ["--recipe", recipe_path, "--out", out_dir]))

    @mcp.tool(annotations=_ann(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
    def cad_review_export(ctx: Context, basis_path: str, blocks_root: str, out_dir: str,
                          drawing_number: str, title: str, revision: str,
                          issue_date: str, pdf: bool = True,
                          presentation_path: str | None = None,
                          color_mode: Literal["realistic","monochrome"] = "realistic") -> list[TextContent]:
        """Export a non-construction STEP/neutral-loader/views/DXF review bundle.

        Preserves all holds and failed checks. Every bundle is immutable and
        issuable=false; it cannot bypass cad_publish or the typed clearance gate.
        Optional strict presentation JSON binds hash-checked representative STEP
        references and geometry-derived structure schedules, never executable code.
        """
        args = ["--basis", basis_path, "--blocks", blocks_root, "--out", out_dir,
                "--drawing", drawing_number, "--title", title, "--revision", revision,
                "--issue-date", issue_date,"--color-mode",color_mode]
        if not pdf:
            args.append("--no-pdf")
        if presentation_path is not None:
            args.extend(["--presentation", presentation_path])
        return _text(_run_cad_module("engineering_utils.cad.review", args))

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def cad_basis_validate(ctx: Context, basis_path: str, blocks_root: str) -> list[TextContent]:
        """Validate a project's ``cad_basis.yaml`` against its block library.

        Checks spec conformance, line/route reconciliation, nozzle size and
        approach direction, slope, MSS support spans and separation rules.
        Errors block an issue; warnings are reported.
        """
        from engineering_utils.cad.basis import basis_hash, basis_report, validate_basis

        try:
            basis, interfaces = _load(basis_path, blocks_root)
        except Exception as exc:
            return _text(f"Failed to load: {exc}")

        findings = validate_basis(basis, interfaces)
        return _text(f"{basis_report(findings)}\n\ndesign hash: {basis_hash(basis)}")

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def cad_edit_preview(
        ctx: Context, basis_path: str, blocks_root: str, edits: list[dict[str, Any]]
    ) -> list[TextContent]:
        """Show what a set of edits would break, WITHOUT writing anything.

        Each edit is a typed operation with a mandatory ``reason``: e.g.
        ``{"op": "block_move", "tag": "P-02", "reason": "...",
        "placement": {"position_mm": [...]}}``. Run this before any apply —
        it is the only way to see that moving a pump orphaned the line feeding it.
        """
        from pydantic import TypeAdapter

        from engineering_utils.cad.edits import Edit, preview_edits

        try:
            basis, interfaces = _load(basis_path, blocks_root)
            parsed = [TypeAdapter(Edit).validate_python(e) for e in edits]
        except Exception as exc:
            return _text(f"Rejected: {exc}")

        _, impact = preview_edits(basis, parsed, interfaces)
        return _text(impact.summary())

    @mcp.tool(annotations=_ann(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
    def cad_edit_apply(
        ctx: Context, basis_path: str, blocks_root: str, edits: list[dict[str, Any]],
        allow_breakage: bool = False, expected_basis_hash: str | None = None,
    ) -> list[TextContent]:
        """Apply edits transactionally and write the basis back.

        Refuses when the change introduces a new error unless
        ``allow_breakage`` is set — a designer may legitimately work through an
        intermediate broken state, but it has to be asked for.

        Args:
            expected_basis_hash: the design hash last read from this basis, as
                reported by cad_basis_validate or a previous cad_edit_apply.
                The fleet runs concurrent pe-cad sessions against one file, and
                without this the second write silently discards the first
                session's work. Pass it and a stale edit is refused instead.
        """
        from pydantic import TypeAdapter

        from engineering_utils.cad.basis import (
            basis_hash, basis_write_lock, dump_basis)
        from engineering_utils.cad.edits import Edit, EditRejected, apply_edits

        # Load, apply and write under one lock. expected_basis_hash alone
        # compares against the copy this process already read, so two editors
        # who both passed the correct original hash both wrote and the first
        # edit vanished. The fleet runs concurrent pe-cad sessions.
        with basis_write_lock(basis_path):
            try:
                basis, interfaces = _load(basis_path, blocks_root)
                parsed = [TypeAdapter(Edit).validate_python(e) for e in edits]
            except Exception as exc:
                return _text(f"Rejected: {exc}")

            try:
                updated, impact = apply_edits(basis, parsed, interfaces,
                                              allow_breakage=allow_breakage,
                                              expected_basis_hash=expected_basis_hash)
            except EditRejected as exc:
                return _text(f"NOT APPLIED — nothing was written.\n{exc}")
            except ValueError as exc:
                return _text(f"NOT APPLIED — nothing was written.\n{exc}")

            dump_basis(updated, basis_path,
                       expected_disk_hash=basis_hash(basis))
        return _text(f"{impact.summary()}\n\nwrote {basis_path} "
                     f"(design hash {basis_hash(updated)})")

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def cad_clash_gate(ctx: Context, basis_path: str, blocks_root: str,
                       ) -> list[TextContent]:
        """Run the solid-vs-solid interference gate on a basis.

        Compiles the basis to solids and intersects them with real booleans.
        Needs a geometry kernel; reports UNAVAILABLE rather than passing when
        there is none.
        """
        return _text(_clash_via_kernel(basis_path, blocks_root))
        try:
            from engineering_utils.cad.basis import basis_hash, load_basis, load_interfaces
            from engineering_utils.cad.geometry import interference_from_basis
            from engineering_utils.cad.publish import _compiled_lines
            from engineering_utils.cad.interference import format_report

            import FreeCAD  # noqa: F401,PLC0415 - probing for a local kernel

            basis = load_basis(basis_path)
            interfaces = load_interfaces(blocks_root)
            run = interference_from_basis(basis, interfaces, model_hash=basis_hash(basis),
                                          compiled_lines=_compiled_lines(basis, interfaces))
            return _text(format_report(run))
        except ImportError:
            return _text(_clash_via_kernel(basis_path, blocks_root))
        except Exception as exc:
            return _text(f"CLASH GATE FAILED: {exc}. This is NOT a pass.")

    @mcp.tool(annotations=_ann(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
    def cad_publish(
        ctx: Context, basis_path: str, blocks_root: str, out_dir: str,
        drawing_number: str, title: str, revision: str, issue_date: str,
        plane: str = "xy", units: str = "in", pdf: bool = True,
        hazard_path: str | None = None,
    ) -> list[TextContent]:
        """Compose and issue a GA sheet from the basis.

        Validates the basis and the sheet first and writes nothing if either
        fails — a DXF on disk gets emailed; a validation message does not.

        Args:
            hazard_path: hazard_basis/v1 JSON carrying the APPROVED
                hazardous-area inputs. Without it the compliance lens is
                vacuous and CODE-NO-AREA-CLASSIFICATION blocks the issue, so
                an issue is not reachable without one. CAD verifies geometry
                against a classification somebody competent approved; it does
                not originate one, which is why this is a separate document
                rather than a field on the basis.

        Issuing is NOT idempotent: a second call at the same drawing number and
        revision is refused because the first issue is immutable.
        """
        from engineering_utils.cad.basis import basis_report
        from engineering_utils.cad.publish import publish_project

        try:
            hazard = None
            if hazard_path:
                from engineering_utils.cad.hazardous_area import HazardBasis
                hazard = HazardBasis.model_validate_json(
                    Path(hazard_path).read_text())
            basis, _ = _load(basis_path, blocks_root)
            findings, manifest = publish_project(
                basis, blocks_root, out_dir, drawing_number=drawing_number, title=title,
                revision=revision, issue_date=issue_date, plane=plane, units=units,
                pdf=pdf, hazard=hazard)
        except Exception as exc:
            return _text(f"Publish failed: {exc}")

        report = basis_report(findings)
        if manifest is None:
            return _text(f"{report}\n\nNOT ISSUED — the basis does not validate; "
                         "nothing was written.")
        if not manifest.issuable:
            problems = "\n".join(f"  {p}" for p in manifest.validation_problems)
            return _text(f"{report}\n\nNOT ISSUED — the sheet does not validate:\n{problems}")
        files = "\n".join(f"  {f}" for f in manifest.files)
        return _text(f"{report}\n\nissued {manifest.drawing_number} rev "
                     f"{manifest.revision} (hash {manifest.model_input_hash})\n{files}\n"
                     f"open holds printed: {len(manifest.open_holds)}")
