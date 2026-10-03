"""Typed MCP adapters for shared asset jobs; geometry stays in the OCP worker."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from engineering_utils.cad.asset_jobs import PatternExportJob,FittingExportJob,NativeIngestJob,AssetBindJob,FabricationJob,CuttingDxfJob,PrototypeBatchJob,PrototypeRequest,FittingRequest,catalog
from engineering_utils.cad.pattern_model import PatternSpec
from engineering_utils.cad.fabrication import PlanarPart,FabricationRule
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
    def cad_library_catalog(family:str|None=None,asset_root:str|None=None,query:str|None=None,limit:int=20,offset:int=0,view:str="summary")->dict[str,Any]:
        """Discover equipment patterns and parametric piping fittings, sizes and examples.

        Optionally search latest vendor asset metadata under asset_root; binding
        rechecks source bytes. The fittings section lists SCH80 PVC/CPVC pipe,
        90-degree socket elbow, tee and socket flange requests for cad_fitting_export,
        plus metallic flanges. Use view=summary for compact paged discovery,
        view=schema with an exact family for its bounded parameters, or view=fittings
        for fitting coverage. Plastic elbows use sourced curved paths; independent
        molded-body profile qualification remains held.
        Membership is distinct from procurement and source/service qualification.
        """
        try:return {"ok":True,"result":catalog(family,asset_root=asset_root,query=query,limit=limit,offset=offset,view=view)}
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
    def cad_prototype_materialize(requests:list[PrototypeRequest],cache_root:str)->dict[str,Any]:
        """Build requested parameterized equipment prototypes once; verify cached artifacts.

        Returns compact immutable STEP/interface references. Repeated placements
        share prototypes; procurement and independent qualification remain held.
        """
        return run_job(PrototypeBatchJob(requests=requests,cache_root=cache_root))

    @mcp.tool()
    def cad_fitting_export(request:FittingRequest,out_dir:str,slug:str,preview:bool=True)->dict[str,Any]:
        """Export parametric fittings: SCH80 PVC/CPVC pipe, elbow90, tee, flange_socket; metallic flanges.

        Discover sizes and request examples with cad_library_catalog(view="fittings").
        Plastic elbow90 bodies follow a sourced tangent curved path; independent
        product-profile qualification remains held. Other kinds are rejected.
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


    @mcp.tool()
    def cad_fabrication_export(part:PlanarPart,out_dir:str,rule:FabricationRule|None=None)->dict[str,Any]:
        """Generate one source-bound planar part STEP, cutting DXF and measured held PDF.

        Saved STEP holes and DXF profiles are measured and validated. Sourced
        fabricator rules check applicable minima; geometry does not grant approval.
        No bent-sheet unfolding or section/detail view coverage is asserted.
        """
        return run_job(FabricationJob(part=part,out_dir=out_dir,rule=rule))

    @mcp.tool()
    def cad_cutting_dxf_validate(path:str,operation_layers:dict[str,str])->dict[str,Any]:
        """Validate saved closed planar cutting contours with explicit operation layers."""
        return run_job(CuttingDxfJob(path=path,operation_layers=operation_layers))
