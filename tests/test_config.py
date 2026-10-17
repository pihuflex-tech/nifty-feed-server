import os
import tempfile
import config

def test_load_env_file_basic(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "TEST_VAR1=hello\n"
        "TEST_VAR2='quoted value' # inline comment\n"
        "TEST_VAR3=\"double quoted\"\n"
        "TEST_PORT=8080\n",
        encoding="utf-8"
    )
    loaded = config.load_env_file(str(env_file))
    assert loaded.get("TEST_VAR1") == "hello"
    assert loaded.get("TEST_VAR2") == "quoted value"
    assert loaded.get("TEST_VAR3") == "double quoted"
    assert loaded.get("TEST_PORT") == "8080"


def test_validate_credentials():
    # Test missing credentials detection
    old_key = config.API_KEY
    try:
        config.API_KEY = ""
        valid, missing = config.validate_credentials()
        assert not valid
        assert "ANGEL_API_KEY" in missing
    finally:
        config.API_KEY = old_key


def test_numeric_parsing():
    assert config.parse_int("5555", 8080) == 5555
    assert config.parse_int("invalid", 8080) == 8080
    assert config.parse_float("12.5", 10.0) == 12.5
    assert config.parse_float("invalid", 10.0) == 10.0
