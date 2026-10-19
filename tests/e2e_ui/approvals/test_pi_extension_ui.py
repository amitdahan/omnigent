"""Pi select and confirm dialogs render and answer through the real browser."""

from __future__ import annotations

import threading

import httpx
import pytest
from playwright.sync_api import Page, expect

_APPROVAL_CARD = '[data-testid="approval-card"]'
_FORM = '[data-testid="ask-user-question-form"]'
_SUBMIT = '[data-testid="ask-user-question-submit"]'

_MOCK_ELICITATION_TIMEOUT_MS = 15_000

# The permission-gate shape a Pi extension uses: the tool asks before acting.
_SELECT_TITLE = 'deploy_prod requests approval for build "v2.4.0"'
_ALLOW_ONCE = "Allow once"
_DENY = "Deny"
_CONFIRM_TITLE = "Confirm production deploy"
_CONFIRM_MESSAGE = "Really deploy v2.4.0 to production right now?"


def _post_extension_ui(
    base_url: str,
    session_id: str,
    request: dict,
    holder: dict,
) -> threading.Thread:
    """Post the blocking hook on a thread while the browser answers it."""
    elicitation_id = f"elicit_pi_{request['method']}_{'cd' * 12}"

    def _post() -> None:
        try:
            resp = httpx.post(
                f"{base_url}/v1/sessions/{session_id}/hooks/pi-extension-ui",
                json={"elicitation_id": elicitation_id, "request": request},
                timeout=60.0,
            )
            resp.raise_for_status()
            holder["response"] = resp.json()
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()
    return thread


def _join_hook(thread: threading.Thread, holder: dict) -> dict:
    """Join the hook thread and return its JSON verdict."""
    thread.join(timeout=30)
    if "error" in holder:
        error = holder["error"]
        raise AssertionError(f"pi extension UI hook failed: {error}") from error
    assert "response" in holder, "pi extension UI hook returned no verdict"
    return holder["response"]


@pytest.mark.timeout(90)
def test_pi_extension_select_renders_options_and_returns_the_choice(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """``ctx.ui.select`` → AskUserQuestion form → chosen label flows back to Pi."""
    base_url, session_id = seeded_session

    holder: dict = {}
    hook_thread = _post_extension_ui(
        base_url,
        session_id,
        {
            "id": "uuid-select",
            "method": "select",
            "title": _SELECT_TITLE,
            "options": [_ALLOW_ONCE, "Allow always", _DENY],
        },
        holder,
    )

    page.goto(f"{base_url}/c/{session_id}")

    card = (
        page.locator(f'{_APPROVAL_CARD}[data-state="pending"]')
        .filter(has=page.locator(_FORM))
        .first
    )
    expect(card).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    # The header names Pi, not the default "Claude has questions" — the prompt
    # came from a Pi extension and a reviewer of the card should see that.
    expect(card.get_by_text("Pi needs your input")).to_be_visible()

    form = card.locator(_FORM)
    expect(form.get_by_text(_SELECT_TITLE, exact=True)).to_be_visible()
    # Only the extension's own options: Pi's select cannot accept free text, so
    # a custom-input row would let Submit answer with something it never offered.
    for label in (_ALLOW_ONCE, "Allow always", _DENY):
        expect(form.get_by_text(label, exact=True)).to_be_visible()

    form.get_by_role("radio", name=_ALLOW_ONCE).check()
    form.locator(_SUBMIT).click()

    expect(page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first).to_be_visible(
        timeout=_MOCK_ELICITATION_TIMEOUT_MS
    )

    verdict = _join_hook(hook_thread, holder)
    assert verdict["action"] == "accept", verdict
    assert _ALLOW_ONCE in str(verdict.get("content")), verdict


@pytest.mark.timeout(90)
def test_pi_extension_confirm_renders_a_binary_card_and_approves(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """``ctx.ui.confirm`` → binary Approve/Reject card → Approve returns accept."""
    base_url, session_id = seeded_session

    holder: dict = {}
    hook_thread = _post_extension_ui(
        base_url,
        session_id,
        {
            "id": "uuid-confirm",
            "method": "confirm",
            "title": _CONFIRM_TITLE,
            "message": _CONFIRM_MESSAGE,
        },
        holder,
    )

    page.goto(f"{base_url}/c/{session_id}")

    card = page.locator(f'{_APPROVAL_CARD}[data-state="pending"]').first
    expect(card).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    # A confirm is binary — no question form, just the two buttons.
    expect(card.locator(_FORM)).to_have_count(0)
    # Both halves of the dialog survive: the mapper joins title and message with
    # a blank line, which the card must not collapse into one run-on sentence.
    expect(card.get_by_text(_CONFIRM_TITLE, exact=False)).to_be_visible()
    expect(card.get_by_text(_CONFIRM_MESSAGE, exact=False)).to_be_visible()

    card.get_by_role("button", name="Approve").click()

    responded = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(responded).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)

    verdict = _join_hook(hook_thread, holder)
    assert verdict["action"] == "accept", verdict
