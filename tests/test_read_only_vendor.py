from __future__ import annotations

from pathlib import Path

VENDOR = (
    Path(__file__).resolve().parents[1]
    / "platforms"
    / "x-api"
    / "src"
    / "x_api"
    / "vendor"
    / "bird-search"
)


def test_vendor_contains_only_read_only_search_operation() -> None:
    source_files = [
        path
        for path in VENDOR.rglob("*")
        if path.suffix in {".js", ".mjs", ".json"} and path.name != "package.json"
    ]
    source = "\n".join(path.read_text() for path in source_files)
    forbidden = [
        "CreateTweet",
        "DeleteTweet",
        "CreateRetweet",
        "DeleteRetweet",
        "FavoriteTweet",
        "UnfavoriteTweet",
        "CreateBookmark",
        "DeleteBookmark",
        "CreateFriendship",
        "DestroyFriendship",
        "statuses/update",
        "media/upload",
        "sweet-cookie",
        "cookieSource",
        "--auth-token",
        "--ct0",
    ]

    assert "SearchTimeline" in source
    for token in forbidden:
        assert token not in source
