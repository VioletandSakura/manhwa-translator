from app.security.redaction import mask_secret, redact_text


def test_mask_secret_never_reveals_short_key() -> None:
    assert mask_secret("abc") == "***"


def test_mask_secret_shows_only_edges() -> None:
    assert mask_secret("secret-123456") == "se***56"


def test_redact_text_removes_bearer_and_api_key() -> None:
    message = "Authorization: Bearer test-sensitive-token api_key=secret-value"

    redacted = redact_text(message)

    assert "test-sensitive-token" not in redacted
    assert "secret-value" not in redacted
    assert "***" in redacted
