"""
Application Configuration and Environment Loader
Loads configuration from .env and system environment with strict validation.
"""

import os
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def parse_int(value: Optional[str], default: int, min_val: int = 1, max_val: int = 65535) -> int:
    """Safely parses an integer within bounds."""
    if value is None:
        return default
    try:
        val = int(str(value).strip())
        return val if min_val <= val <= max_val else default
    except (ValueError, TypeError):
        return default


def parse_float(value: Optional[str], default: float, min_val: float = 0.0) -> float:
    """Safely parses a positive float."""
    if value is None:
        return default
    try:
        val = float(str(value).strip())
        return val if val >= min_val else default
    except (ValueError, TypeError):
        return default


def load_env_file(filepath: str) -> Dict[str, str]:
    """
    Parses a .env file safely, supporting:
    - Quoted values ('...', "...")
    - Trailing inline comments
    - Whitespace trimming
    """
    env_vars: Dict[str, str] = {}
    if not os.path.isfile(filepath):
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, raw_val = line.split("=", 1)
            key = key.strip()
            raw_val = raw_val.strip()

            # Handle quoted strings or inline comments
            val = ""
            if raw_val.startswith('"'):
                end_quote = raw_val.find('"', 1)
                val = raw_val[1:end_quote] if end_quote != -1 else raw_val[1:]
            elif raw_val.startswith("'"):
                end_quote = raw_val.find("'", 1)
                val = raw_val[1:end_quote] if end_quote != -1 else raw_val[1:]
            else:
                # Unquoted: strip trailing comment if any
                val = raw_val.split("#", 1)[0].strip()

            if key:
                env_vars[key] = val
                if key not in os.environ:
                    os.environ[key] = val

    return env_vars


# Load from default .env
load_env_file(ENV_PATH)

# Network settings
FEED_HOST = os.getenv("FEED_HOST", "127.0.0.1").strip()
FEED_PORT = parse_int(os.getenv("FEED_PORT"), default=5555)
CACHE_EXPIRY_HOURS = parse_float(os.getenv("CACHE_HOURS"), default=12.0)

# Market Session Settings
MARKET_TIMEZONE = os.getenv("MARKET_TIMEZONE", "Asia/Kolkata").strip()
SESSION_CUTOFF_TIME = os.getenv("SESSION_CUTOFF_TIME", "15:30:00").strip()
STALE_TICK_SECONDS = parse_float(os.getenv("STALE_TICK_SECONDS"), default=15.0)

# SmartAPI Credentials
API_KEY = os.getenv("ANGEL_API_KEY", "").strip()
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "").strip()
PIN = os.getenv("ANGEL_PIN", "").strip()
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "").strip()

# Angel One Static URLs & Cache Files
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_CACHE_FILE = os.path.join(BASE_DIR, "OpenAPIScripMaster.json")
FILTERED_CACHE_FILE = os.path.join(BASE_DIR, "filtered_nifty_scrips.json")

# Fixed Indices
NIFTY_SPOT_TOKEN = "99926000"  # Nifty 50 Cash Index (Exchange Type 1)


def validate_credentials() -> Tuple[bool, List[str]]:
    """
    Validates required broker credentials without printing or exposing secret values.
    Returns (is_valid, list_of_missing_fields).
    """
    fields = [
        ("ANGEL_API_KEY", API_KEY),
        ("ANGEL_CLIENT_CODE", CLIENT_CODE),
        ("ANGEL_PIN", PIN),
        ("ANGEL_TOTP_SECRET", TOTP_SECRET),
    ]
    missing = [name for name, val in fields if not val or "your_" in val.lower()]
    return len(missing) == 0, missing