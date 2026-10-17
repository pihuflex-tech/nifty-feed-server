import logging
import security

def test_sanitize_data_dictionary():
    raw = {
        "api_key": "my_secret_key",
        "clientcode": "R12345",
        "password": "secret_pin",
        "totp": "654321",
        "safe_key": "safe_value",
        "nested": {
            "jwtToken": "token_abc",
            "normal": 42
        }
    }
    sanitized = security.sanitize_data(raw)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["clientcode"] == "[REDACTED]"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["totp"] == "[REDACTED]"
    assert sanitized["safe_key"] == "safe_value"
    assert sanitized["nested"]["jwtToken"] == "[REDACTED]"
    assert sanitized["nested"]["normal"] == 42


def test_redact_string():
    msg = "Headers: {'X-PrivateKey': 'secret123'}, Request: {'clientcode': 'R561', 'password': '639', 'totp': '123'}"
    redacted = security.redact_string(msg)
    assert "secret123" not in redacted
    assert "R561" not in redacted
    assert "639" not in redacted
    assert "123" not in redacted
    assert "[REDACTED]" in redacted


def test_sensitive_data_filter_handler(tmp_path):
    log_file = tmp_path / "test_safe.log"
    logger = logging.getLogger("test_sec_logger")
    logger.setLevel(logging.DEBUG)
    
    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.addFilter(security.SensitiveDataFilter())
    logger.addHandler(handler)

    test_payload = "Login failed for clientcode: 'MY_CLIENT_999' with password: 'MY_SECRET_PASS'"
    logger.error(test_payload)
    handler.close()
    logger.removeHandler(handler)

    content = log_file.read_text(encoding="utf-8")
    assert "MY_CLIENT_999" not in content
    assert "MY_SECRET_PASS" not in content
    assert "[REDACTED]" in content


def test_patch_smartconnect():
    success = security.patch_smartconnect_logging()
    # Should succeed since SmartApi is installed
    assert success is True
    from SmartApi.smartConnect import SmartConnect
    assert getattr(SmartConnect, "_is_security_patched", False) is True

