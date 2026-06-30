from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from x_api.errors import ServiceError
from x_api.models import Engagement, SearchItem

_TWITTER_TIMESTAMP = "%a %b %d %H:%M:%S %z %Y"


def _non_negative_int(value: Any, *, default: int | None) -> int | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) > 10 and raw[10] == "T":
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            parsed = datetime.strptime(raw, _TWITTER_TIMESTAMP)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_tweet(tweet: dict[str, Any]) -> SearchItem | None:
    tweet_id = tweet.get("id")
    text = tweet.get("text")
    author = tweet.get("author")
    created_at = _parse_timestamp(tweet.get("createdAt"))

    if not isinstance(tweet_id, str) or not tweet_id:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(author, dict):
        return None

    handle = author.get("username")
    if not isinstance(handle, str) or not handle.strip() or created_at is None:
        return None
    handle = handle.strip().lstrip("@")

    author_name = author.get("name")
    if not isinstance(author_name, str) or not author_name.strip():
        author_name = handle

    return SearchItem(
        id=tweet_id,
        url=f"https://x.com/{handle}/status/{tweet_id}",
        text=text.strip(),
        created_at=created_at,
        author_handle=handle,
        author_name=author_name.strip(),
        engagement=Engagement(
            likes=_non_negative_int(tweet.get("likeCount"), default=0) or 0,
            reposts=_non_negative_int(tweet.get("retweetCount"), default=0) or 0,
            replies=_non_negative_int(tweet.get("replyCount"), default=0) or 0,
            quotes=_non_negative_int(tweet.get("quoteCount"), default=0) or 0,
            views=_non_negative_int(tweet.get("viewCount"), default=0) or 0,
        ),
    )


def normalize_tweets(raw_items: list[dict[str, Any]]) -> list[SearchItem]:
    items: list[SearchItem] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        item = normalize_tweet(raw_item)
        if item is None or item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        items.append(item)

    if raw_items and not items:
        raise ServiceError(
            "invalid_response",
            "X returned results in an unsupported format",
        )
    return items
