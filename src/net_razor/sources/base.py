from __future__ import annotations

from typing import Protocol

from net_razor.clock import ResolvedWindow
from net_razor.models import FetchResult, SourceName


class Source(Protocol):
    """A pure source adapter.

    Implementations must not touch the audit store, mutate global state, or read
    the wall clock: given the same request and window they return the same
    ``FetchResult`` (modulo live upstream data). All side effects live in the
    audit wrapper around this boundary.
    """

    name: SourceName

    async def fetch(self, request: object, window: ResolvedWindow) -> FetchResult:
        """Fetch normalized items for a request within an absolute time window."""
