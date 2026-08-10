from __future__ import annotations

from typing import Protocol

from net_razor.clock import ResolvedWindow
from net_razor.models import FetchResult, SourceName


class Source(Protocol):
    """A source adapter.

    The contract, in order of how much it matters:

    1. **Never touch the audit store.** Recording is the wrapper's job. A source
       that reads or writes the trail makes the audit boundary a lie, and makes
       the source untestable without a database. (``yt_transcript`` reads stored
       transcripts, but ``App`` does the lookup and passes the result in.)
    2. **Never read the wall clock for time-window logic.** The window arrives
       resolved. Identical inputs plus an identical window must produce identical
       upstream calls, which is what "deterministic" means here.
    3. **Don't put results into ``effective_request``.** It records what was
       asked for, not what came back; anything else belongs in ``meta``.

    What a source *may* do, and what the rule used to forbid by accident: pace
    itself. ``XSource`` holds a semaphore, a monotonic timestamp and a sleep to
    serialize requests for one account, because the alternative is bursty traffic
    against session cookies whose worst case is a suspended account. That is
    deliberate self-limiting, not hidden state, and it belongs beside the source
    that needs it.
    """

    name: SourceName

    async def fetch(self, request: object, window: ResolvedWindow) -> FetchResult:
        """Fetch normalized items for a request within an absolute time window."""
