"""Five strict adapters; the shared resolver owns all state and domain logic."""
from __future__ import annotations
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field
from mcp.types import ToolAnnotations
from engineering_utils.cad.resolution.commands import (
    Query, Inspect, Answer, Question, QuestionAnswer, ItemState, Validate,
    Preview, Apply, Revalidate, Doctor, Dispatch,
)
from engineering_utils.cad.resolution.mcp_service import invoke

ListRequest = Annotated[Query | Inspect, Field(discriminator='operation')]
AnswerRequest = Annotated[Answer | Question | QuestionAnswer | ItemState | Validate, Field(discriminator='operation')]
ApplyRequest = Annotated[Preview | Apply | Revalidate, Field(discriminator='operation')]


def register_resolution_tools(mcp):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def resolution_list(project_ref: str, run_id: UUID, request: ListRequest) -> dict[str, Any]:
        """Read a registered resolution run: board, item evidence, ready packets or questions.

        The host registry selects trusted sources and its audited persistent store.
        Initialize a run with the shared CLI; this read does not create a run.
        """
        return await invoke(project_ref, run_id, request)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def resolution_answer(project_ref: str, run_id: UUID, request: AnswerRequest) -> dict[str, Any]:
        """Record proposed answers/questions, validate evidence, defer or refresh an item.

        Writes require actor/reason and item changes compare their current version.
        A question answer never applies a design value or closes an engineering hold.
        """
        return await invoke(project_ref, run_id, request)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def resolution_dispatch(project_ref: str, run_id: UUID, request: Dispatch) -> dict[str, Any]:
        """Dispatch one current packet through a configured, live-qualified specialist runtime.

        Requests identify an existing packet hash. Bounded packets have no canonical
        write scope; exact retries reuse the recorded dispatch and its evidence.
        """
        return await invoke(project_ref, run_id, request)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def resolution_apply(project_ref: str, run_id: UUID, request: ApplyRequest) -> dict[str, Any]:
        """Preview/apply a validated answer through its owning writer, or revalidate the basis.

        Apply requires the exact preview hash, same actor/reason, explicit apply
        scope and persistent ledger. Unconfigured writers fail closed. Current
        typed clearance and source evidence govern findings; questions alone
        cannot close holds. Read the preview before requesting application.
        """
        return await invoke(project_ref, run_id, request)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def resolution_doctor(project_ref: str, run_id: UUID, request: Doctor) -> dict[str, Any]:
        """Compare declared specialist capabilities with live tools, schema pins and availability.

        Reports retain missing-corpus and unavailable-method findings, exclude
        launch credentials, and do not refresh existing agent-host inventories.
        """
        return await invoke(project_ref, run_id, request)
