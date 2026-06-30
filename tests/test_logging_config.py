from __future__ import annotations

from x_api.logging_config import query_hash, redact_text


def test_query_hash_is_stable_and_does_not_contain_query() -> None:
    digest = query_hash("private search terms")

    assert digest == query_hash("private search terms")
    assert len(digest) == 12
    assert "private" not in digest


def test_redaction_removes_credentials_and_sensitive_headers() -> None:
    text = (
        "AUTH_TOKEN=secret-auth; ct0=secret-csrf "
        "Authorization: Bearer public-or-private-token\n"
        "Cookie: auth_token=secret-auth; ct0=secret-csrf"
    )

    redacted = redact_text(text, ["secret-auth", "secret-csrf"])

    assert "secret-auth" not in redacted
    assert "secret-csrf" not in redacted
    assert "public-or-private-token" not in redacted
    assert "Authorization:" not in redacted
    assert "Cookie:" not in redacted
