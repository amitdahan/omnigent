"""Bundled Pi resources survive upload and a separate server/runner workspace."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tests._helpers.server_runner import server_runner
from tests._helpers.session import bind_session_runner, bundle_files, post_session_bundle
from tests.e2e._harness_probes import cli_unavailable_reason
from tests.e2e.conftest import get_mock_requests
from tests.e2e.omnigent._pi_mock_gateway import write_pi_gateway_config
from tests.e2e.omnigent.conftest import configure_mock_llm, reset_mock_llm

_EXT_MARKER = "OMNI_PI_BUNDLE_EXT_ACTIVE"
_AGENTSMD_MARKER = "OMNI_PI_BUNDLE_AGENTSMD_MARKER"
_SKILL_MARKER = "OMNI_PI_BUNDLE_SKILL_MARKER"
_AGENT_PROMPT = "You are the bundle test agent."
_TURN_TIMEOUT = 180.0

_pytest_pi_unavailable = cli_unavailable_reason("pi")
pytestmark = pytest.mark.skipif(
    _pytest_pi_unavailable is not None,
    reason=(
        "pi bundle-extensions e2e requires a runnable 'pi' CLI; "
        f"{_pytest_pi_unavailable}. Install/fix Pi to run this test."
    ),
)


def _build_bundle(bundle: Path, model: str) -> None:
    """Write an extension, skill and context file visible only through the bundle."""
    ext_dir = bundle / ".pi" / "extensions" / "omni-marker"
    ext_dir.mkdir(parents=True)
    (ext_dir / "index.js").write_text(
        "module.exports = function (pi) {\n"
        '  pi.on("before_agent_start", async (event) => {\n'
        f"    return {{ systemPrompt: `${{event.systemPrompt}}\\n\\n{_EXT_MARKER}` }};\n"
        "  });\n"
        "};\n",
        encoding="utf-8",
    )
    (bundle / "AGENTS.md").write_text(
        f"# Bundle guidance\n\n{_AGENTSMD_MARKER}: always answer like a pirate.\n",
        encoding="utf-8",
    )
    skill_dir = bundle / ".pi" / "skills" / "grilling"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: grilling\ndescription: {_SKILL_MARKER} grilling tips\n---\n# Grilling\n",
        encoding="utf-8",
    )
    (bundle / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "name": "pi_ext_bundle_agent",
                "prompt": _AGENT_PROMPT,
                "executor": {"model": model, "config": {"harness": "pi"}},
                "os_env": {"type": "caller_process", "cwd": ".", "sandbox": {"type": "none"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _create_session(client: httpx.Client, bundle: Path, runner_id: str) -> str:
    resp = post_session_bundle(
        client.post,
        "/v1/sessions",
        bundle_files(
            {
                str(path.relative_to(bundle)): path.read_bytes()
                for path in bundle.rglob("*")
                if path.is_file()
            }
        ),
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["session_id"]
    bind_session_runner(client.patch, "", session_id, runner_id)
    return session_id


def _drive_turn_to_terminal(client: httpx.Client, session_id: str, prompt: str) -> dict[str, Any]:
    body = {
        "type": "message",
        "data": {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
    }
    client.post(f"/v1/sessions/{session_id}/events", json=body).raise_for_status()
    deadline = time.monotonic() + _TURN_TIMEOUT
    seen_running = False
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get(f"/v1/sessions/{session_id}").json()
        status = last.get("status")
        if status in ("running", "waiting"):
            seen_running = True
        turn_items = [
            item
            for item in last.get("items", [])
            if item.get("type") not in ("resource_event",)
            and not (item.get("type") == "message" and item.get("data", {}).get("role") == "user")
        ]
        if status == "failed" or (status == "idle" and (seen_running or turn_items)):
            return last
        time.sleep(1.0)
    raise AssertionError(
        f"session {session_id} did not become terminal in {_TURN_TIMEOUT}s; last={last}"
    )


def _captured_system_prompts(mock_url: str, model: str) -> list[str]:
    prompts: list[str] = []
    for req in get_mock_requests(mock_url, key=model):
        if not isinstance(req, dict):
            continue
        for msg in req.get("messages", []):
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content")
                if isinstance(content, str):
                    prompts.append(content)
        instructions = req.get("system") or req.get("instructions")
        if isinstance(instructions, str):
            prompts.append(instructions)
    return prompts


def test_pi_bundle_extensions_reach_session_cwd(
    omnigent_repo_root: Path,
    mock_credentials_env: dict[str, str],
    mock_llm_server_url: str,
    tmp_path: Path,
) -> None:
    """All bundled markers reach the model alongside the authored prompt."""
    model = f"mock-pi-bundle-{uuid.uuid4().hex[:8]}"
    reset_mock_llm(mock_llm_server_url)
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "Hello from the mock model."}] * 4,
        key=model,
    )

    bundle = tmp_path / "myagent"
    bundle.mkdir()
    _build_bundle(bundle, model)

    config_home = tmp_path / "omnigent-config"
    write_pi_gateway_config(config_home, mock_url=mock_llm_server_url, model=model)

    env = {
        "OMNIGENT_CONFIG_HOME": str(config_home),
        "OMNIGENT_PI_PATH": os.environ.get("OMNIGENT_PI_PATH"),
    }
    bootstrap = (
        "import sys\nfrom omnigent.cli import main\n"
        f"sys.argv.extend(['--agent', {str(bundle)!r}])\nmain()\n"
    )
    with server_runner(
        tmp_path / "stack",
        base_env=mock_credentials_env,
        server_env=env,
        server_bootstrap=bootstrap,
        server_cwd=omnigent_repo_root,
    ) as stack:
        stack.start_runner(env=env, cwd=omnigent_repo_root)
        with httpx.Client(base_url=stack.base_url, timeout=30) as client:
            session_id = _create_session(client, bundle, stack.runner_id)
            snapshot = _drive_turn_to_terminal(
                client, session_id, "Introduce yourself in one sentence."
            )

    assert snapshot.get("status") != "failed", (
        f"pi turn failed instead of exercising the extension path: "
        f"{snapshot.get('last_task_error') or snapshot.get('error')}"
    )

    system_prompts = _captured_system_prompts(mock_llm_server_url, model)
    assert system_prompts, (
        "mock LLM captured no system prompt — the pi turn never reached the "
        "model, so the extension-loading path was not exercised"
    )
    joined = "\n".join(system_prompts)

    assert _EXT_MARKER in joined, (
        "bundle's project-local .pi/extensions extension did not load — its "
        f"before_agent_start marker {_EXT_MARKER!r} is absent from the system "
        "prompt pi sent to the model (pi ran in the session workspace, not the "
        "bundle directory)."
    )
    assert _AGENTSMD_MARKER in joined, (
        "bundle-root AGENTS.md did not reach pi — its marker "
        f"{_AGENTSMD_MARKER!r} is absent from the system prompt (pi's cwd-based "
        "context loader never saw the bundle directory)."
    )
    assert _SKILL_MARKER in joined, (
        "bundle's .pi/skills skill did not reach pi — its description marker "
        f"{_SKILL_MARKER!r} is absent from the skill index in the system prompt."
    )
    assert _AGENT_PROMPT in joined, (
        "the agent's own prompt was lost while appending the bundle resources"
    )
