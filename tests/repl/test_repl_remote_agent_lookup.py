"""REPL agent lookup on a remote server: an unknown agent name is reported safely."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from omnigent_client import RegisteredAgent
from omnigent_client._sessions import Session

from omnigent.repl._repl import _SessionsChatReplAdapter


@pytest.mark.asyncio
async def test_remote_repl_unknown_agent_error_is_escaped() -> None:
    """A missing agent name keeps its LookupError, with control characters escaped."""

    async def _resolve_agent(name: str) -> object:
        raise LookupError(f"No agent named {name!r} is registered on this server.")

    adapter = _SessionsChatReplAdapter(
        SimpleNamespace(sessions=SimpleNamespace(resolve_agent=_resolve_agent)),  # type: ignore[arg-type]
        "evil\x1b[2J",
    )

    # The REPL renders exceptions at its UI boundary; keep the LookupError contract.
    with pytest.raises(LookupError) as excinfo:
        await adapter._create_session_from_registered_agent(pending_debug=False)

    assert "\x1b" not in str(excinfo.value) and "\\x1b" in str(excinfo.value)


@pytest.mark.asyncio
async def test_remote_repl_preserves_selected_catalog_identity() -> None:
    """Session creation and runner selection do not resolve a selected name again."""
    created: list[str] = []
    harnesses: list[str | None] = []

    async def _unexpected_lookup(_name: str) -> RegisteredAgent:
        raise AssertionError("the selected catalog entry already has an identity")

    async def _resolve_runner(*, harness: str | None, **_kwargs: object) -> str:
        harnesses.append(harness)
        return "runner_existing"

    async def _create(agent_id: str, **_kwargs: object) -> Session:
        created.append(agent_id)
        return Session(id="conv_new", agent_id=agent_id, status="idle", created_at=0)

    client = SimpleNamespace(
        sessions=SimpleNamespace(
            resolve_agent=_unexpected_lookup,
            resolve_online_runner=_resolve_runner,
            create_from_agent_id=_create,
        )
    )
    adapter = _SessionsChatReplAdapter(
        client,  # type: ignore[arg-type]
        "reviewer",
        registered_agent=RegisteredAgent(id="ag_selected", harness="openai-agents"),
    )

    await adapter._create_session_from_registered_agent(pending_debug=False)

    assert created == ["ag_selected"]
    assert harnesses == ["openai-agents"]
