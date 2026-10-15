import os

# Zero-dependency .env file loader
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val

# Network settings
FEED_HOST = os.getenv("FEED_HOST", "127.0.0.1")
FEED_PORT = int(os.getenv("FEED_PORT", "5555"))
CACHE_EXPIRY_HOURS = float(os.getenv("CACHE_HOURS", "12"))

# SmartAPI Credentials
API_KEY = os.getenv("ANGEL_API_KEY", "")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE", "")
PIN = os.getenv("ANGEL_PIN", "")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# Angel One Static URLs & Cache Files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_CACHE_FILE = os.path.join(BASE_DIR, "OpenAPIScripMaster.json")
FILTERED_CACHE_FILE = os.path.join(BASE_DIR, "filtered_nifty_scrips.json")

# Fixed Indices
NIFTY_SPOT_TOKEN = "99926000"  # Nifty 50 Cash Index (Exchange Type 1)