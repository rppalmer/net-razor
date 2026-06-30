from __future__ import annotations

import json
from pathlib import Path

import pytest
from x_api.errors import ServiceError
from x_api.normalization import normalize_tweets

FIXTURES = Path(__file__).parent / "fixtures"


def test_normalization_uses_real_ids_timestamps_and_engagement() -> None:
    raw_items = json.loads((FIXTURES / "raw_tweets.json").read_text())

    items = normalize_tweets(raw_items)

    assert len(items) == 1
    item = items[0]
    assert item.id == "1234567890"
    assert item.url == "https://x.com/example_user/status/1234567890"
    assert item.created_at.isoformat() == "2026-05-20T14:30:00+00:00"
    assert item.engagement.model_dump() == {
        "likes": 7,
        "reposts": 3,
        "replies": 2,
        "quotes": 1,
        "views": 99,
    }


def test_normalization_rejects_entirely_unsupported_nonempty_payload() -> None:
    with pytest.raises(ServiceError) as exc_info:
        normalize_tweets([{"unexpected": "shape"}])

    assert exc_info.value.error_type == "invalid_response"
