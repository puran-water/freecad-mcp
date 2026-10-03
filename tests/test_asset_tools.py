import json
import subprocess
import pytest
from freecad_mcp import asset_tools
from engineering_utils.cad.asset_jobs import PatternExportJob
from engineering_utils.cad.pattern_model import representative_pattern


def job():
    return PatternExportJob(spec=representative_pattern("pump_centrifugal"),out_dir="a'; arbitrary text",slug="fixture.pump")


def test_worker_uses_typed_stdin_and_fixed_module(monkeypatch,tmp_path):
    kernel=tmp_path/"python";kernel.touch()
    monkeypatch.setenv("PURANOS_BUILD123D_PYTHON",str(kernel))
    captured={}
    def run(argv,**kwargs):
        captured.update(argv=argv,**kwargs)
        return subprocess.CompletedProcess(argv,0,stdout='{"ok":true,"result":{"issuable":false}}',stderr="")
    monkeypatch.setattr(asset_tools.subprocess,"run",run)
    assert asset_tools.run_job(job())["ok"]
    assert captured["argv"]==[str(kernel),"-m","engineering_utils.cad.asset_jobs","--request","-"]
    assert json.loads(captured["input"])["out_dir"]==job().out_dir
    assert not captured.get("shell")


@pytest.mark.parametrize("code,output,error",[(1,'{"ok":true}',"CAD_WORKER_FAILED"),(0,'not-json',"CAD_WORKER_PROTOCOL"),(0,'[]',"CAD_WORKER_PROTOCOL"),(0,'{"ok":false,"error":"missing source"}',"missing source")])
def test_worker_failure_cannot_become_success(monkeypatch,tmp_path,code,output,error):
    kernel=tmp_path/"python";kernel.touch();monkeypatch.setenv("PURANOS_BUILD123D_PYTHON",str(kernel))
    monkeypatch.setattr(asset_tools.subprocess,"run",lambda *a,**k:subprocess.CompletedProcess(a[0],code,stdout=output,stderr=""))
    result=asset_tools.run_job(job())
    assert result["ok"] is False and result["error"]==error


def test_missing_worker_reports_unavailable(monkeypatch):
    monkeypatch.delenv("PURANOS_BUILD123D_PYTHON",raising=False)
    assert asset_tools.run_job(job())["error"]=="CAD_WORKER_UNAVAILABLE"


def test_registered_asset_inputs_are_the_shared_contracts():
    registered={}
    class MCP:
        def tool(self):
            def decorate(fn):registered[fn.__name__]=fn;return fn
            return decorate
    asset_tools.register_asset_tools(MCP())
    assert set(registered)=={"cad_library_catalog","cad_pattern_export","cad_fitting_export","cad_asset_ingest","cad_asset_bind"}
    assert registered["cad_library_catalog"]("motor_iec")["ok"]
    assert not registered["cad_library_catalog"]("unknown")["ok"]


def test_mcp_protocol_exposes_bounded_contract_and_preserves_payload_failure(monkeypatch):
    import asyncio
    from fastmcp import Client,FastMCP
    async def check():
        server=FastMCP("asset-protocol-test");asset_tools.register_asset_tools(server)
        async with Client(server) as client:
            tools={tool.name:tool for tool in await client.list_tools()}
            assert "request" in tools["cad_asset_bind"].inputSchema["properties"]
            good=await client.call_tool("cad_library_catalog",{"family":"motor_iec"})
            assert good.data["ok"] and good.data["result"]["choices"]["frame"]==["90S"]
            fittings=good.data['result']['fittings']
            assert fittings['export_tool']=='cad_fitting_export'
            elbow=fittings['families']['thermoplastic']['request_example']
            assert elbow['kind']=='elbow90'
            assert 'intersecting-cylinder' in tools['cad_fitting_export'].description
            membrane=await client.call_tool('cad_library_catalog',{'family':'membrane_rack'})
            adapted=membrane.data['result']['source_adaptation']
            assert adapted['spec_field']=='header_reference.adaptation'
            assert adapted['array_limits']['modules_max']==1024
            assert 'header_reference' in tools['cad_pattern_export'].inputSchema['properties']['spec']['properties']
            assert adapted['design_schema']['properties']['module_area_m2']['enum']==[6,12]
            assert 'adaptation' in json.dumps(tools['cad_pattern_export'].inputSchema)
            from pathlib import Path
            from engineering_utils.cad.membrane_rack import MembraneProfiles,make_rack
            from engineering_utils.cad.membrane_design import MembraneAdaptation
            ref=Path(__file__).resolve().parents[4]/'libs/engineering-utils/tests/cad/fixtures/suke-header-reference.json'
            source={'authority':'assumed','ref_id':'MCP test','note':'Synthetic test source and duty'}
            profiles=MembraneProfiles.model_validate({'schema_version':'cad_membrane_profiles/v1',
                'source_header':json.loads(ref.read_text()),'modules':{'6m2':{
                    'geometry_relpath':'module.step','sha256':'0'*64,'source':source}}})
            design=MembraneAdaptation(module_area_m2=6,peak_permeate_flux_lmh=40,
                scour_air_m3_h_per_m2=.6,air_volume_basis='actual_at_header',pipe_schedule='SCH40',duty_source=source)
            spec=make_rack(profiles,columns=2,rows=3,design=design)
            captured=[]
            def dispatch(job):
                captured.append(job)
                return {'ok':True,'result':{'worker_requested':True}}
            monkeypatch.setattr(asset_tools,'run_job',dispatch)
            args={'spec':spec.model_dump(mode='json'),'out_dir':'fixture','slug':'fixture.membrane','preview':False}
            accepted=await client.call_tool('cad_pattern_export',args)
            assert accepted.data['ok'] and len(captured)==1
            assert captured[0].spec.header_reference.adaptation.module_area_m2==6
            args['spec']['parameters']['module_height_mm']=1500
            with pytest.raises(Exception,match='scaling is forbidden'):
                await client.call_tool('cad_pattern_export',args)
            assert len(captured)==1
            fitting=await client.call_tool('cad_fitting_export',dict(request=dict(family='thermoplastic',
                kind='tee',material='CPVC',schedule='SCH80',nominal_size='2.5in'),
                out_dir='fixture-cpvc',slug='fixture.cpvc',preview=False))
            assert fitting.data['ok'] and captured[-1].request.material=='CPVC'
            assert captured[-1].request.kind=='tee'
            accepted_elbow=await client.call_tool('cad_fitting_export',dict(request=elbow,
                out_dir='fixture-elbow',slug='fixture.elbow',preview=False))
            assert accepted_elbow.data['ok'] and captured[-1].request.kind=='elbow90'
            with pytest.raises(Exception,match='SCH80'):
                await client.call_tool('cad_fitting_export',dict(request=dict(family='thermoplastic',
                    kind='tee',material='CPVC',schedule='SCH40',nominal_size='2.5in'),
                    out_dir='fixture-cpvc',slug='fixture.cpvc',preview=False))
            bad=await client.call_tool("cad_library_catalog",{"family":"unknown"})
            assert bad.data["ok"] is False
            assert "unknown" in bad.data["error"]
    asyncio.run(check())
