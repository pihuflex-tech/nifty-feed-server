"""
High-Performance Thread-Safe HTTP JSON Broadcaster (Termux & Mobile Optimized)
Exposes market snapshots, health checks, readiness probes, metrics, and multi-expiry selection over HTTP with CORS.
Features zero-allocation dirty-flag cached payload delivery for minimum CPU and battery consumption.
"""

import os
import json
import time
import copy
import urllib.parse
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Dict, Any, Optional, Callable, Tuple, List

import config
import scrip_master

# Optimize thread stack size for Termux / mobile memory efficiency (256 KB per thread)
try:
    threading.stack_size(262144)
except Exception:
    pass

DATA_LOCK = threading.Lock()

# Ephemeral state store
FEED_STORE: Dict[str, Any] = {
    "spot": {
        "symbol": "NIFTY",
        "ltp": 0.0,
        "token": config.NIFTY_SPOT_TOKEN,
        "updated": "",
    },
    "expiry": "",
    "available_expiries": [],
    "contracts": {},
}

# Operational diagnostics and metrics
METRICS: Dict[str, Any] = {
    "start_time": time.time(),
    "feed_status": "DISCONNECTED",  # DISCONNECTED, CONNECTING, CONNECTED, STALE, RECONNECTING
    "last_tick_time": 0.0,
    "last_tick_timestamp": "",
    "ticks_received": 0,
    "http_requests": 0,
    "reconnect_count": 0,
    "last_error": "",
}

ACTIVE_SERVER: Optional[HTTPServer] = None
SET_EXPIRY_CALLBACK: Optional[Callable[[str], Tuple[bool, str]]] = None

# Zero-allocation payload cache for Termux / low-CPU performance
_CACHE_DIRTY = True
_CACHED_PAYLOAD_BYTES: bytes = b""


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handles concurrent HTTP requests without blocking."""
    daemon_threads = True
    allow_reuse_address = True


def get_snapshot() -> Dict[str, Any]:
    """
    Creates a thread-safe copy of the current state under lock,
    allowing downstream operations to inspect state without holding locks.
    """
    with DATA_LOCK:
        return {
            "spot": dict(FEED_STORE["spot"]),
            "expiry": FEED_STORE.get("expiry", ""),
            "available_expiries": list(FEED_STORE.get("available_expiries", [])),
            "contracts": {
                k: {
                    "CE": dict(v["CE"]),
                    "PE": dict(v["PE"]),
                }
                for k, v in FEED_STORE["contracts"].items()
            },
        }


def get_snapshot_bytes() -> bytes:
    """
    Returns pre-encoded JSON bytes using a dirty-flag cache.
    Eliminates repetitive dict copying and serialization on frequent polling.
    """
    global _CACHE_DIRTY, _CACHED_PAYLOAD_BYTES
    with DATA_LOCK:
        if not _CACHE_DIRTY and _CACHED_PAYLOAD_BYTES:
            return _CACHED_PAYLOAD_BYTES

        snapshot = {
            "spot": dict(FEED_STORE["spot"]),
            "expiry": FEED_STORE.get("expiry", ""),
            "available_expiries": list(FEED_STORE.get("available_expiries", [])),
            "contracts": {
                k: {
                    "CE": dict(v["CE"]),
                    "PE": dict(v["PE"]),
                }
                for k, v in FEED_STORE["contracts"].items()
            },
        }
        _CACHED_PAYLOAD_BYTES = json.dumps(snapshot).encode("utf-8")
        _CACHE_DIRTY = False
        return _CACHED_PAYLOAD_BYTES


def set_feed_status(status: str, error_msg: str = "") -> None:
    """Updates operational feed status."""
    with DATA_LOCK:
        METRICS["feed_status"] = status
        if error_msg:
            METRICS["last_error"] = error_msg


def _record_tick_locked(now: Optional[float] = None) -> None:
    """Internal helper to update tick metrics and dirty-flag while DATA_LOCK is held."""
    global _CACHE_DIRTY
    tick_time = now if now is not None else time.time()
    METRICS["ticks_received"] += 1
    METRICS["last_tick_time"] = tick_time
    METRICS["last_tick_timestamp"] = scrip_master.get_market_now().strftime("%H:%M:%S")
    METRICS["feed_status"] = "CONNECTED"
    _CACHE_DIRTY = True


def record_tick() -> None:
    """Records an incoming price tick event."""
    with DATA_LOCK:
        _record_tick_locked()


def get_current_feed_status(now: Optional[float] = None) -> str:
    """Returns dynamic feed status taking staleness into account."""
    current_time = now if now is not None else time.time()
    with DATA_LOCK:
        status = METRICS["feed_status"]
        last_tick = METRICS["last_tick_time"]
    if status == "CONNECTED" and last_tick > 0 and (current_time - last_tick) > config.STALE_TICK_SECONDS:
        return "STALE"
    return status


def record_reconnect() -> None:
    """Increments the reconnect counter."""
    with DATA_LOCK:
        METRICS["reconnect_count"] += 1


class FeedHandler(BaseHTTPRequestHandler):
    """HTTP request handler supporting REST endpoints, multi-expiry, and CORS."""

    def _send_payload(self, status_code: int, payload: bytes) -> None:
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json_response(self, status_code: int, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self._send_payload(status_code, payload)

    def do_GET(self) -> None:
        with DATA_LOCK:
            METRICS["http_requests"] += 1

        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/") or "/"

        if path == "/":
            # Primary market snapshot served from zero-allocation byte cache
            payload = get_snapshot_bytes()
            self._send_payload(200, payload)

        elif path in ("/ui", "/index.html"):
            index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    ui_html = f.read()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(ui_html)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(ui_html)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            self._send_json_response(404, {"error": "index.html not found"})

        elif path == "/expiries":
            # Returns list of all available active expiries
            with DATA_LOCK:
                resp = {
                    "current_expiry": FEED_STORE.get("expiry", ""),
                    "available_expiries": list(FEED_STORE.get("available_expiries", [])),
                }
            self._send_json_response(200, resp)

        elif path == "/set_expiry":
            # Dynamic expiry selection
            query_params = urllib.parse.parse_qs(parsed_url.query)
            target = query_params.get("expiry", [None])[0]
            if not target:
                self._send_json_response(400, {"error": "Missing 'expiry' query parameter (YYYY-MM-DD)"})
                return

            if SET_EXPIRY_CALLBACK:
                success, msg = SET_EXPIRY_CALLBACK(target)
                if success:
                    self._send_json_response(200, {"status": "ok", "expiry": target, "message": msg})
                else:
                    self._send_json_response(400, {"error": msg})
            else:
                self._send_json_response(501, {"error": "Dynamic expiry switching not registered on server"})

        elif path == "/health":
            # Liveness probe
            now = time.time()
            with DATA_LOCK:
                uptime = round(now - METRICS["start_time"], 1)
                last_tick_age = (
                    round(now - METRICS["last_tick_time"], 1)
                    if METRICS["last_tick_time"] > 0
                    else None
                )
                status = "ok"

            self._send_json_response(200, {
                "status": status,
                "service": "nifty-feed-server",
                "uptime_seconds": uptime,
                "last_tick_age_seconds": last_tick_age,
            })

        elif path == "/ready":
            # Readiness probe: verifies instruments loaded and initial spot seeded
            now = time.time()
            with DATA_LOCK:
                contracts_count = len(FEED_STORE["contracts"])
                spot_ltp = FEED_STORE["spot"]["ltp"]
                feed_status = METRICS["feed_status"]
                last_tick_time = METRICS["last_tick_time"]

            effective_status = feed_status
            if feed_status == "CONNECTED" and last_tick_time > 0 and (now - last_tick_time) > config.STALE_TICK_SECONDS:
                effective_status = "STALE"

            is_ready = contracts_count > 0 and (spot_ltp > 0 or effective_status in ("CONNECTED", "STALE"))
            self._send_json_response(200 if is_ready else 503, {
                "ready": is_ready,
                "feed_status": effective_status,
                "contracts_initialized": contracts_count,
                "spot_seeded": spot_ltp > 0,
            })

        elif path == "/metrics":
            # Diagnostic metrics
            now = time.time()
            with DATA_LOCK:
                metrics_copy = dict(METRICS)
                metrics_copy["uptime_seconds"] = round(now - METRICS["start_time"], 1)
                last_tick_time = METRICS["last_tick_time"]
                metrics_copy["last_tick_age_seconds"] = (
                    round(now - last_tick_time, 1)
                    if last_tick_time > 0
                    else None
                )
                metrics_copy["contracts_count"] = len(FEED_STORE["contracts"])
                metrics_copy["expiry"] = FEED_STORE.get("expiry", "")

                if metrics_copy["feed_status"] == "CONNECTED" and last_tick_time > 0 and (now - last_tick_time) > config.STALE_TICK_SECONDS:
                    metrics_copy["feed_status"] = "STALE"

            self._send_json_response(200, metrics_copy)

        else:
            # 404 for unknown endpoints
            self._send_json_response(404, {
                "error": "Not Found",
                "path": self.path,
                "available_routes": ["/", "/ui", "/expiries", "/set_expiry?expiry=YYYY-MM-DD", "/health", "/ready", "/metrics"],
            })

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Silence continuous polling access logs to save CPU and disk I/O on Termux
        return


def create_http_server(host: str = config.FEED_HOST, port: int = config.FEED_PORT) -> HTTPServer:
    """Creates a ThreadedHTTPServer instance."""
    return ThreadedHTTPServer((host, port), FeedHandler)


def start_http_server(host: str = config.FEED_HOST, port: int = config.FEED_PORT) -> None:
    """Starts the HTTP broadcaster server."""
    global ACTIVE_SERVER
    ACTIVE_SERVER = create_http_server(host, port)
    ACTIVE_SERVER.serve_forever()


def stop_http_server() -> None:
    """Gracefully stops the active HTTP server."""
    global ACTIVE_SERVER
    if ACTIVE_SERVER:
        ACTIVE_SERVER.shutdown()
        ACTIVE_SERVER.server_close()
        ACTIVE_SERVER = None


def init_contracts(
    contracts_template: Dict[str, Any],
    expiry: str = "",
    available_expiries: Optional[List[str]] = None,
) -> None:
    """Pre-allocates the contract strike dictionary and expiry metadata in memory."""
    global _CACHE_DIRTY
    with DATA_LOCK:
        FEED_STORE["contracts"] = contracts_template
        if expiry:
            FEED_STORE["expiry"] = expiry
        if available_expiries is not None:
            FEED_STORE["available_expiries"] = available_expiries
        _CACHE_DIRTY = True


def update_spot(ltp: float, time_str: str) -> None:
    """Updates the spot price and timestamp under single lock acquisition."""
    with DATA_LOCK:
        FEED_STORE["spot"]["ltp"] = ltp
        FEED_STORE["spot"]["updated"] = time_str
        _record_tick_locked()


def update_contract(strike_str: str, opt_type: str, ltp: float) -> None:
    """Updates an option contract price under single lock acquisition."""
    with DATA_LOCK:
        if strike_str in FEED_STORE["contracts"]:
            FEED_STORE["contracts"][strike_str][opt_type]["ltp"] = ltp
            _record_tick_locked()