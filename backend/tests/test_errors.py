from app.core.errors import redact_text


def test_redact_text_removes_known_secret_and_auth_header() -> None:
    value = "api_key=visible Authorization: Basic abc123"

    redacted = redact_text(value, ("visible",))

    assert "visible" not in redacted
    assert "abc123" not in redacted
    assert redacted.count("[REDACTED]") == 2
