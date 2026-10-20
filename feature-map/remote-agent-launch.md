# Remote agent launch

Users run a registered agent with `omnigent run --server <url>`, choose from
the server and their own registrations, and send a prompt interactively or
with `-p`. New sessions use the selected registration; existing sessions keep
their own agents. Without a matching online runner, a new session can launch
one on this machine's connected host.

## Sub-features

- `catalog`: fresh and used servers offer the same registered agents; unused
  registrations remain visible and session-only uploads are not candidates.
- `selection`: one candidate is automatic; several offer a numbered/name menu
  with source and harness. `--agent <name>` selects explicitly; ambiguous
  headless runs fail without creating a session.
- `identity`: the selected registration survives same-name uploads and startup;
  server registrations take precedence over same-named user registrations.
- `runner-lifecycle`: adopt a matching online runner or launch on this machine's
  suitable connected host. One-shots stop only the runner they launched;
  interactive host-launched runners remain host-managed.
- `existing-session`: resume uses the existing session's agent; fork copies the
  source without a catalog picker. Headless fork is rejected.
- `errors`: empty catalogs, unknown names, unreachable servers, authentication,
  access denial, malformed responses, and incomplete pagination fail clearly.

## How to get to it (user POV)

- Interactive: `omnigent run --server <url>`; choose a registered agent and type
  a prompt. Repeat after a conversation exists to check unused registrations.
- Explicit selection: `omnigent run --server <url> --agent <name>`.
- One-shot: add `-p "hi"`; with several registrations, omit `--agent` once to
  check the selection error rather than an interactive prompt.
- Connected-host launch: start `omnigent host --server <url>` on this machine,
  then use either new-session entry point with no matching runner online.
- Existing session: `omnigent run --server <url> --resume <session-id>` or
  `omnigent run --server <url> --fork <session-id>`.

## Driving it with the repro environment

Preconditions: read [Verify Omnigent](skills/verify-omnigent/SKILL.md). The tests
below use their own isolated services or mocked transport; do not start the
default verification environment for them. Its prestarted runner would mask
the host-launch case. Use this checkout's frozen dependencies, a local mock
model, isolated homes, and only fixture-owned processes. Interactive process
coverage requires `pexpect`; a skipped variant is not proof.

- `catalog`, `selection`, and `identity`: component cases in
  `tests/cli/test_chat.py` cover fresh/used catalogs, pagination, server/user
  collisions, and headless ambiguity. The real-service scenario
  `tests/e2e/test_remote_url_chat_fresh_session_2437.py::test_run_one_shot_on_fresh_server_asks_for_agent_then_answers_with_it`
  checks ambiguity, explicit selection, and unchanged choices after the first
  session. The real-service adapter scenario
  `tests/e2e/test_remote_url_chat_fresh_session_2437.py::test_repl_adapter_creates_session_without_bundle`
  uploads a differing same-name bundle and verifies the registered reply
  without another name lookup; it does not drive the terminal UI.
- Interactive selection:
  `tests/cli/test_chat.py::test_pick_agent_menu_accepts_a_name_or_index`
  checks menu selection by name and index with a mocked catalog.
  For terminal proof, register A and B on an isolated server, run A once, then
  run the interactive CLI, choose unused B by name, and send `hi`. Capture the
  menu, B's scripted reply, exit status, and its catalog ID from the session.
  The complete menu-to-reply journey still needs a dedicated PTY regression.
- `runner-lifecycle`: both parameters of
  `tests/e2e/test_remote_url_chat_fresh_session_2437.py::test_run_launches_a_runner_on_this_machines_host`
  run the real CLI against an isolated server and host with no initial runner.
  Check the reply, exit status, selected agent ID, host ID, workspace, and
  one-shot cleanup versus interactive runner ownership. Component matrices in
  `tests/cli/test_remote_launch.py`,
  `tests/repl/test_repl_remote_agent_lookup.py`, and
  `tests/frontends/sdk/test_sessions_namespace.py` cover readiness decoding,
  harness matching, other machines, adopted runners, and redundant binding.
  `tests/cli/test_chat.py::test_one_shot_host_launch_routes_actual_http_requests`
  checks creation, session reads, and cleanup headers with and without bearer
  auth and host sharding. The interactive startup matrix checks creation and
  later session reads without bearer auth. These use mocked HTTP, not a live
  sharded deployment.
- `existing-session`: `tests/cli/test_chat.py` covers source-session lookup,
  missing resume harnesses, and fork independence from a failing or ambiguous
  catalog. `tests/cli/test_cli.py` covers early rejection of fork with `-p`.
  These regressions are component checks, not a complete process-level fork or
  resume journey.
- `errors`: `tests/cli/test_chat.py` and
  `tests/cli/test_cli_diagnostics.py` check failure messages and recovery advice;
  `tests/cli/test_login_databricks.py` covers the malformed-proxy URL probe.
  Mocked HTTP errors do not verify real authentication or access control.
  Mixed valid/malformed catalog rows must fail instead of auto-selecting the
  valid survivor; missing optional harness metadata remains supported.
  Host-listing failures name `GET /v1/hosts` and the HTTP status rather than
  advising users to start a host. Only a 404 retains the older-server fallback.

Run component coverage without browser flags:

```sh
uv run --no-sync pytest -q tests/cli/test_chat.py tests/cli/test_cli.py \
  tests/cli/test_remote_launch.py tests/cli/test_cli_diagnostics.py \
  tests/cli/test_login_databricks.py tests/repl/test_repl_remote_agent_lookup.py \
  tests/cli/test_session_host.py tests/cli/test_cli_auth.py \
  tests/frontends/sdk/test_sessions_namespace.py --reruns 0
```

Run the own-environment process scenarios with the default mock model:

```sh
uv run --no-sync pytest -q \
  tests/e2e/test_remote_url_chat_fresh_session_2437.py \
  --reruns 0
```

For terminal evidence, record the command, transcript or timed terminal video,
exit status, tested revision, and read-only session/runner checks. Keep media
outside the tracked tree; upload it as a PR attachment. A video of one entry
point does not establish the others.

## Gotchas

- Local YAML upload and native harness launch are separate workflows, not
  additional entries in the remote catalog picker. See
  [Custom agents](custom-agents.md) for Settings and
  [Login and host authentication](login-and-host-auth.md) for real sign-in.
- This machine's host is required because the workspace is the CLI's local cwd.
  Do not select another machine or stop a developer's existing runner to test.
- Resume and fork do not gain automatic offline-runner relaunch. Interactive
  resume of an old snapshot with no agent name can still fall back to a menu;
  headless resume without a usable harness must fail rather than guess.
- Catalog IDs identify registrations, not immutable spec revisions. Optional
  user-listing compatibility errors differ from a failed primary catalog.
- Local mock-model process tests do not prove live provider authentication,
  OS-sandbox startup, or compatibility with pinned older server/runner builds.
  Record those separately when actually exercised; map consistency checks do
  not run the user journeys.
