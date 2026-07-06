from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from net_razor.audit.store import AuditStore
from net_razor.clock import Clock
from net_razor.logging import query_hash
from net_razor.models import EvidenceItem, ServiceErrorItem

_log = logging.getLogger("net_razor.audit")


@dataclass
class CallHandle:
    """Per-invocation audit handle. The tool body records its payload and the
    context manager closes the call with a final status."""

    id: str
    _store: AuditStore
    _clock: Clock
    outcome: str = "ok"
    response: dict[str, Any] | None = None
    _source: str | None = None
    _recorded: bool = False

    def record(
        self,
        *,
        effective_request: dict[str, Any],
        items: list[EvidenceItem],
        raw: dict[str, dict[str, Any]],
        errors: list[ServiceErrorItem],
    ) -> None:
        """Persist normalized items, full raw payloads, and handled errors."""

        self._store.record_payload(
            call_id=self.id,
            source=self._source,
            effective_request=effective_request,
            items=items,
            raw=raw,
            errors=errors,
            created_at=self._clock.now().isoformat(),
        )
        self._recorded = True
        if errors:
            self.outcome = "completed_with_errors"

    def set_response(self, response: dict[str, Any]) -> None:
        self.response = response


class AuditRecorder:
    """Middleware at the tool boundary. Every tool call — direct or fan-out —
    opens a call record here, so nothing reaches a source unaudited."""

    def __init__(self, store: AuditStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    @asynccontextmanager
    async def call(
        self,
        *,
        tool: str,
        source: str | None,
        request: dict[str, Any],
        parent_id: str | None = None,
    ) -> AsyncIterator[CallHandle]:
        call_id = uuid4().hex
        started = time.perf_counter()
        self._store.open_call(
            call_id=call_id,
            parent_id=parent_id,
            tool=tool,
            source=source,
            request=request,
            created_at=self._clock.now().isoformat(),
        )
        handle = CallHandle(id=call_id, _store=self._store, _clock=self._clock, _source=source)
        _log.info(
            "call_started call_id=%s tool=%s source=%s qhash=%s",
            call_id,
            tool,
            source or "-",
            query_hash(str(request.get("query") or request.get("topic") or "")),
        )
        try:
            yield handle
        except Exception as exc:
            handle.outcome = "failed"
            if not handle._recorded:
                self._store.record_payload(
                    call_id=call_id,
                    source=source,
                    effective_request={},
                    items=[],
                    raw={},
                    errors=[
                        ServiceErrorItem(
                            type="request_failed",
                            message=f"{tool} failed",
                            details={"reason": str(exc)},
                        )
                    ],
                    created_at=self._clock.now().isoformat(),
                )
            self._finish(handle, started)
            _log.warning("call_failed call_id=%s error=%s", call_id, type(exc).__name__)
            raise
        self._finish(handle, started)
        _log.info(
            "call_finished call_id=%s outcome=%s item_count=%s",
            call_id,
            handle.outcome,
            0 if handle.response is None else len(handle.response.get("items", []) or []),
        )

    def _finish(self, handle: CallHandle, started: float) -> None:
        self._store.close_call(
            call_id=handle.id,
            status=handle.outcome,
            response=handle.response,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            finished_at=self._clock.now().isoformat(),
        )
