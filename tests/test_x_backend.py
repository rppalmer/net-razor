from __future__ import annotations

import sys
from pathlib import Path

import pytest

from net_razor.config import Settings
from net_razor.errors import SourceError
from net_razor.sources.x.bird_backend import BirdXSearchBackend
from net_razor.sources.x.subprocess_runner import run_json_subprocess


def _settings(**overrides) -> Settings:
    base = dict(auth_token="tok", ct0="ct", x_search_retry_backoff_seconds=0,
                x_search_max_attempts=3)
    base.update(overrides)
    return Settings(**base)


async def _no_sleep(_delay: float) -> None:
    return None


class _ScriptedRunner:
    """Yields queued responses (dicts) or raises queued SourceErrors, per attempt."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def __call__(self, executable, script_path, payload, environment, timeout):
        self.calls += 1
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _backend(runner, **settings_overrides) -> BirdXSearchBackend:
    backend = BirdXSearchBackend(_settings(**settings_overrides), process_runner=runner,
                                 sleep=_no_sleep)

    async def _fake_node() -> str:
        return "node"

    backend._ensure_node = _fake_node  # bypass real Node discovery
    return backend


@pytest.mark.asyncio
async def test_success_returns_dict_items():
    runner = _ScriptedRunner([{"ok": True, "items": [{"id": "1"}, "junk", {"id": "2"}]}])
    result = await _backend(runner).search("q", 5, "latest")
    assert result == [{"id": "1"}, {"id": "2"}]  # non-dicts dropped


@pytest.mark.asyncio
async def test_retryable_error_retries_then_succeeds():
    runner = _ScriptedRunner([
        {"ok": False, "error": {"type": "rate_limited", "retryable": True}},
        {"ok": True, "items": []},
    ])
    result = await _backend(runner).search("q", 5, "latest")
    assert result == []
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_retryable_error_exhausts_attempts():
    runner = _ScriptedRunner([
        {"ok": False, "error": {"type": "rate_limited", "retryable": True}},
        {"ok": False, "error": {"type": "rate_limited", "retryable": True}},
    ])
    with pytest.raises(SourceError) as exc:
        await _backend(runner, x_search_max_attempts=2).search("q", 5, "latest")
    assert exc.value.error_type == "rate_limited"
    assert exc.value.details["attempts"] == 2
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_non_retryable_error_raises_immediately():
    runner = _ScriptedRunner([{"ok": False, "error": {"type": "auth_failed"}}])
    with pytest.raises(SourceError) as exc:
        await _backend(runner).search("q", 5, "latest")
    assert exc.value.error_type == "auth_failed"
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_timeout_from_runner_is_retried():
    runner = _ScriptedRunner([SourceError("timeout", "slow"), {"ok": True, "items": []}])
    result = await _backend(runner).search("q", 5, "latest")
    assert result == []
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_ok_but_non_list_items_is_invalid_response():
    runner = _ScriptedRunner([{"ok": True, "items": "nope"}])
    with pytest.raises(SourceError) as exc:
        await _backend(runner).search("q", 5, "latest")
    assert exc.value.error_type == "invalid_response"


@pytest.mark.asyncio
async def test_missing_credentials_raises_not_configured():
    runner = _ScriptedRunner([{"ok": True, "items": []}])
    backend = _backend(runner, auth_token="", ct0="")
    with pytest.raises(SourceError) as exc:
        await backend.search("q", 5, "latest")
    assert exc.value.error_type == "not_configured"


# --- subprocess_runner (real subprocess round-trip) ------------------------
def _write_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_backend.py"
    script.write_text(body)
    return script


@pytest.mark.asyncio
async def test_runner_parses_valid_protocol_response(tmp_path):
    script = _write_script(
        tmp_path,
        "import sys, json; sys.stdin.read();"
        "print(json.dumps({'protocol_version': 1, 'ok': True, 'items': []}))",
    )
    response = await run_json_subprocess(sys.executable, script, {"action": "search"}, {}, 10)
    assert response["ok"] is True


@pytest.mark.asyncio
async def test_runner_rejects_malformed_output(tmp_path):
    script = _write_script(tmp_path, "print('not json at all')")
    with pytest.raises(SourceError) as exc:
        await run_json_subprocess(sys.executable, script, {}, {}, 10)
    assert exc.value.error_type == "invalid_response"


@pytest.mark.asyncio
async def test_runner_times_out(tmp_path):
    script = _write_script(tmp_path, "import time; time.sleep(5)")
    with pytest.raises(SourceError) as exc:
        await run_json_subprocess(sys.executable, script, {}, {}, 0.3)
    assert exc.value.error_type == "timeout"
