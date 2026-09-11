"""Thin resolver delegation, scoped operation schemas and no secret diagnostics."""
import asyncio
from uuid import uuid4

import pytest
from fastmcp import FastMCP
from freecad_mcp import resolution_tools


def test_resolution_adapters_delegate_to_shared_service(monkeypatch):
    calls = []
    async def invoke(project_ref, run_id, request):
        calls.append((project_ref, run_id, request.operation))
        return {'ok': True, 'result': {'operation': request.operation}}
    monkeypatch.setattr(resolution_tools, 'invoke', invoke)
    mcp = FastMCP('fixture')
    resolution_tools.register_resolution_tools(mcp)
    async def scenario():
        run = uuid4()
        result = await mcp.call_tool('resolution_list', {'project_ref': 'fixture-plant', 'run_id': str(run),
                                                        'request': {'operation': 'board'}})
        assert calls == [('fixture-plant', run, 'board')]
        tools = {t.name: t for t in await mcp.list_tools()}
        assert len(tools) == 5
        assert tools['resolution_list'].annotations.readOnlyHint
        assert tools['resolution_apply'].annotations.destructiveHint
        assert tools['resolution_list'].parameters['additionalProperties'] is False
    asyncio.run(scenario())


def test_read_adapter_rejects_write_operation_before_delegation(monkeypatch):
    async def unexpected(*args):
        raise AssertionError('a read tool accepted a write request')
    monkeypatch.setattr(resolution_tools, 'invoke', unexpected)
    mcp = FastMCP('fixture')
    resolution_tools.register_resolution_tools(mcp)
    async def scenario():
        with pytest.raises(Exception) as error:
            await mcp.call_tool('resolution_list', {'project_ref': 'fixture-plant', 'run_id': str(uuid4()),
                'request': {'operation': 'defer', 'item_key': 'x', 'expected_version': 1, 'actor': 'x', 'reason': 'x'}})
        assert 'read tool accepted' not in str(error.value)
    asyncio.run(scenario())


def test_unconfigured_registry_is_explicit_without_database_or_gui(monkeypatch):
    from engineering_utils.cad.resolution.mcp_service import invoke
    monkeypatch.delenv('PURANOS_CAD_RESOLUTION_REGISTRY', raising=False)
    result = asyncio.run(invoke('fixture-plant', uuid4(), {'operation': 'board'}))
    assert result['ok'] is False and result['error'] == 'RESOLUTION_REGISTRY_UNCONFIGURED'


def test_provider_error_text_cannot_enter_tool_result(monkeypatch):
    from engineering_utils.cad.resolution import mcp_service
    def failed(project):
        raise RuntimeError('postgresql://user:private-password@internal/database')
    monkeypatch.setattr(mcp_service, 'configured_project', failed)
    result = asyncio.run(mcp_service.invoke('fixture-plant', uuid4(), {'operation': 'board'}))
    assert result['ok'] is False and 'private-password' not in str(result)
