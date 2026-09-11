"""Typed MCP adapters for shared asset jobs; geometry stays in the OCP worker."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from engineering_utils.cad.asset_jobs import PatternExportJob,FittingExportJob,NativeIngestJob,AssetBindJob,FittingRequest,catalog
from engineering_utils.cad.pattern_model import PatternSpec
from engineering_utils.cad.warehouse_flanges import FlangeRequest
from engineering_utils.cad.native_ingest import NativeIngestRequest
from engineering_utils.cad.asset_bind import BindRequest
from engineering_utils.cad.asset_ingest_job import CatalogAppend


def run_job(job)->dict[str,Any]:
    kernel=os.environ.get("PURANOS_BUILD123D_PYTHON")
    if not kernel or not Path(kernel).is_file():
        return {"ok":False,"error":"CAD_WORKER_UNAVAILABLE","detail":"Configure PURANOS_BUILD123D_PYTHON; no artifact was produced"}
    import engineering_utils
    env=dict(os.environ,QT_QPA_PLATFORM="offscreen",OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1")
    env["PYTHONPATH"]=str(Path(engineering_utils.__file__).resolve().parents[1])
    try:
        result=subprocess.run([kernel,"-m","engineering_utils.cad.asset_jobs","--request","-"],
            input=job.model_dump_json(),capture_output=True,text=True,timeout=900,env=env)
    except subprocess.TimeoutExpired:
        return {"ok":False,"error":"CAD_WORKER_TIMEOUT","detail":"Inspect the output directory before retrying"}
    except OSError as exc:
        return {"ok":False,"error":"CAD_WORKER_UNAVAILABLE","detail":str(exc)}
    try:payload=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return {"ok":False,"error":"CAD_WORKER_PROTOCOL","exit_code":result.returncode}
    if not isinstance(payload,dict) or not isinstance(payload.get("ok"),bool):
        return {"ok":False,"error":"CAD_WORKER_PROTOCOL","exit_code":result.returncode}
    if result.returncode and payload.get("ok"):
        return {"ok":False,"error":"CAD_WORKER_FAILED","exit_code":result.returncode}
    return payload


def register_asset_tools(mcp):
    @mcp.tool()
    def cad_library_catalog(family:str|None=None,asset_root:str|None=None,query:str|None=None,limit:int=20)->dict[str,Any]:
        """Discover equipment patterns and parametric piping fittings, sizes and examples.

        Optionally search latest vendor asset metadata under asset_root; binding
        rechecks source bytes. The fittings section lists SCH80 PVC/CPVC pipe,
        90-degree socket elbow, tee and socket flange requests for cad_fitting_export,
        plus metallic flanges. Read geometry_status: current plastic elbows are
        intersecting-cylinder approximations with curved-body correction pending.
        Family selects equipment patterns; fitting coverage is returned on every call.
        Membership is distinct from procurement and source/service qualification.
        """
        try:return {"ok":True,"result":catalog(family,asset_root=asset_root,query=query,limit=limit)}
        except ValueError as exc:return {"ok":False,"error":str(exc)}

    @mcp.tool()
    def cad_pattern_export(spec:PatternSpec,out_dir:str,slug:str,preview:bool=True)->dict[str,Any]:
        """Generate one immutable, inspected equipment-pattern STEP/interface/preview bundle.

        Uses the shared build123d worker and bounded PatternSpec. Source membrane
        header_reference.adaptation selects pipe bores against supplied duties
        and velocity limits. It does not establish process duty or approve equipment.
        """
        return run_job(PatternExportJob(spec=spec,out_dir=out_dir,slug=slug,preview=preview))

    @mcp.tool()
    def cad_fitting_export(request:FittingRequest,out_dir:str,slug:str,preview:bool=True)->dict[str,Any]:
        """Export parametric fittings: SCH80 PVC/CPVC pipe, elbow90, tee, flange_socket; metallic flanges.

        Discover sizes and request examples with cad_library_catalog().fittings.
        Current plastic elbow90 bodies are intersecting-cylinder approximations;
        curved molded-body correction is pending. Other kinds are rejected.
        Secondary-source opt-in and a geometry convention are explicit request
        fields for metallic flanges. Thermoplastic sockets use separate source
        laying lengths and insertion-stop ports; pipe geometry uses bd_warehouse.
        STEP reimport is checked. Geometry does not qualify compressed-air service.
        """
        return run_job(FittingExportJob(request=request,out_dir=out_dir,slug=slug,preview=preview))

    @mcp.tool()
    def cad_asset_ingest(request:NativeIngestRequest,out_dir:str|None=None,catalog:CatalogAppend|None=None)->dict[str,Any]:
        """Normalize a hash-bound STEP and measure fluid ports or circular mounting holes.

        Preserves physical occurrences and measures a STEP round-trip. This does
        not independently qualify vendor dimensions, rights, drive engagement or
        mount capacity. Choose an output directory or a CAS catalog append target.
        Catalog append creates a visual candidate; it does not select equipment.
        """
        return run_job(NativeIngestJob(request=request,out_dir=out_dir,catalog=catalog))

    @mcp.tool()
    def cad_asset_bind(request:BindRequest,asset_root:str,lock_path:str)->dict[str,Any]:
        """CAS a governed asset into a project lock with exact or representative use recorded.

        Preserves canonical equipment identity and exact-source requirements.
        A subsequent typed asset_select preview/apply binds the lock into the
        CAD basis; this operation leaves basis geometry and hold status unchanged.
        """
        return run_job(AssetBindJob(request=request,asset_root=asset_root,lock_path=lock_path))
