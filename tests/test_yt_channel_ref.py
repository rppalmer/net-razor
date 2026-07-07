from __future__ import annotations

from net_razor.sources.yt.channel_ref import parse_channel_refs

_UC = "UC" + "a" * 22  # a well-formed channel ID


def test_parses_bare_id_handle_and_username():
    refs = parse_channel_refs(f"{_UC}, @Fireship, plainword")
    assert [(r.kind, r.value) for r in refs] == [
        ("id", _UC),
        ("handle", "Fireship"),
        ("handle", "plainword"),
    ]


def test_parses_channel_urls():
    text = "\n".join([
        f"https://www.youtube.com/channel/{_UC}",
        "https://youtube.com/@Veritasium",
        "https://www.youtube.com/user/GoogleDevelopers",
        "https://youtube.com/c/CustomName",
    ])
    refs = parse_channel_refs(text)
    assert [(r.kind, r.value) for r in refs] == [
        ("id", _UC),
        ("handle", "Veritasium"),
        ("username", "GoogleDevelopers"),
        ("username", "CustomName"),
    ]


def test_per_channel_overrides():
    ref = parse_channel_refs(f"{_UC} | videos=10 days=14")[0]
    assert ref.kind == "id"
    assert ref.videos_per_channel == 10
    assert ref.days == 14


def test_skips_unrecognized_entries():
    refs = parse_channel_refs("https://example.com/@notyoutube, , @ab")
    # non-youtube URL dropped; empty dropped; too-short handle (<3) dropped
    assert refs == []
