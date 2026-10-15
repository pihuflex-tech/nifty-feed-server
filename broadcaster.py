import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import config

DATA_LOCK = threading.Lock()

FEED_STORE = {
    "spot": {
        "symbol": "NIFTY",
        "ltp": 0.0,
        "token": config.NIFTY_SPOT_TOKEN,
        "updated": ""
    },
    "contracts": {}
}

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handles concurrent requests from index.html and multi_strategy_bot.py without blocking."""
    daemon_threads = True
    allow_reuse_address = True

class FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with DATA_LOCK:
            payload = json.dumps(FEED_STORE).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, format, *args):
        return  # Silence polling logs

def start_http_server():
    server = ThreadedHTTPServer((config.FEED_HOST, config.FEED_PORT), FeedHandler)
    server.serve_forever()

def init_contracts(contracts_template: dict):
    with DATA_LOCK:
        FEED_STORE["contracts"] = contracts_template

def update_spot(ltp: float, time_str: str):
    with DATA_LOCK:
        FEED_STORE["spot"]["ltp"] = ltp
        FEED_STORE["spot"]["updated"] = time_str

def update_contract(strike_str: str, opt_type: str, ltp: float):
    with DATA_LOCK:
        if strike_str in FEED_STORE["contracts"]:
            FEED_STORE["contracts"][strike_str][opt_type]["ltp"] = ltp