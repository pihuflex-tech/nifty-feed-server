"""
broadcaster.py
Aggregates Mode 2 ticks, computes real-time Implied Volatility (IV) and Option Greeks,
and publishes structured market snapshots over ZeroMQ PUB on Port 5555.
Optimized for low-latency execution in Termux/Linux environments.
"""

import math
import time
import json
import zmq
import threading
from datetime import datetime
from typing import Dict, Any, Optional

# ZeroMQ Publisher Configuration
ZMQ_PUB_PORT = 5555
ZMQ_BIND_ADDR = f"tcp://*:{ZMQ_PUB_PORT}"

# Global Thread-Safe Market State
_STATE_LOCK = threading.Lock()
MARKET_SNAPSHOT: Dict[str, Any] = {
    "server_status": "STARTING",
    "timestamp": "",
    "reconnect_count": 0,
    "last_error": None,
    "spot": {
        "symbol": "NIFTY 50",
        "ltp": 0.0,
        "high": 0.0,
        "low": 0.0,
        "net_change": 0.0,
        "last_trade_time": ""
    },
    "indices": {
        "india_vix": {"ltp": 0.0, "net_change": 0.0},
        "banknifty": {"ltp": 0.0, "net_change": 0.0}
    },
    "heavyweights": {},
    "futures": {
        "ltp": 0.0,
        "open_interest": 0,
        "volume": 0,
        "net_change": 0.0
    },
    "options": {
        "current_expiry": {},
        "wings": {},
        "next_expiry": {},
        "monthly_expiry": {}
    }
}

# ---------------------------------------------------------
# ZeroMQ Publisher Engine & Background Loop
# ---------------------------------------------------------
_ZMQ_CONTEXT: Optional[zmq.Context] = None
_ZMQ_SOCKET: Optional[zmq.Socket] = None
_RUNNING = True


def init_zmq():
    """Initializes the ZeroMQ PUB socket."""
    global _ZMQ_CONTEXT, _ZMQ_SOCKET
    if _ZMQ_SOCKET is None:
        _ZMQ_CONTEXT = zmq.Context()
        _ZMQ_SOCKET = _ZMQ_CONTEXT.socket(zmq.PUB)
        # Avoid buffering large amounts of stale ticks if subscribers lag
        _ZMQ_SOCKET.setsockopt(zmq.SNDHWM, 50)
        _ZMQ_SOCKET.bind(ZMQ_BIND_ADDR)
        print(f"[*] ZeroMQ PUB Socket bound to {ZMQ_BIND_ADDR}")


def _zmq_broadcast_loop(publish_hz: int = 10):
    """
    Background worker that broadcasts the current MARKET_SNAPSHOT at a steady frequency (default: 100ms interval).
    This avoids saturating IPC/TCP bandwidth on single-core mobile CPU threads in Termux.
    """
    interval = 1.0 / publish_hz
    init_zmq()

    while _RUNNING:
        start_t = time.time()
        try:
            with _STATE_LOCK:
                MARKET_SNAPSHOT["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                # Deep copy or dump to JSON string directly
                payload_str = json.dumps(MARKET_SNAPSHOT)

            # Topic: "NIFTY_MARKET_TICK" followed by JSON payload
            _ZMQ_SOCKET.send_multipart([b"NIFTY_MARKET_TICK", payload_str.encode("utf-8")])
        except Exception as e:
            print(f"[!] ZMQ Broadcast error: {e}")

        elapsed = time.time() - start_t
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)


# Start background ZMQ broadcaster daemon thread
_broadcast_thread = threading.Thread(target=_zmq_broadcast_loop, daemon=True)
_broadcast_thread.start()


# ---------------------------------------------------------
# Pure Python Analytical Greeks Engine (Black-Scholes)
# ---------------------------------------------------------
def _cnd(d: float) -> float:
    """Cumulative normal distribution function approximation."""
    return 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))


def _npdf(d: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * d * d)


def calculate_implied_volatility(
    opt_price: float,
    spot: float,
    strike: float,
    t: float,
    r: float = 0.07,
    opt_type: str = "CE"
) -> float:
    """
    Computes Black-Scholes IV via Newton-Raphson method with fallback bisection.
    r = 7.0% (RBI benchmark/MIBOR reference)
    """
    if opt_price <= 0 or spot <= 0 or strike <= 0 or t <= 0.0001:
        return 0.0

    sigma = 0.15  # 15% seed volatility
    for _ in range(15):
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)

        if opt_type == "CE":
            theo = spot * _cnd(d1) - strike * math.exp(-r * t) * _cnd(d2)
        else:
            theo = strike * math.exp(-r * t) * _cnd(-d2) - spot * _cnd(-d1)

        diff = theo - opt_price
        if abs(diff) < 0.05:
            return round(sigma, 4)

        vega = spot * math.sqrt(t) * _npdf(d1)
        if vega < 1e-5:
            break

        sigma -= diff / vega
        if sigma <= 0.01:
            sigma = 0.01
            break
        if sigma > 2.0:
            sigma = 2.0
            break

    return round(sigma, 4)


def calculate_greeks(
    spot: float,
    strike: float,
    expiry_str: str,
    opt_type: str,
    ltp: float,
    r: float = 0.07
) -> Dict[str, float]:
    """Calculates IV, Delta, Gamma, Vega, and Theta for a given option contract."""
    default_greeks = {"iv": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    if spot <= 0 or strike <= 0 or ltp <= 0 or not expiry_str:
        return default_greeks

    try:
        # Calculate time to expiry in years
        exp_dt = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y")
        now = datetime.now()
        # Set market close at 15:30
        exp_close = exp_dt.replace(hour=15, minute=30, second=0)
        time_diff = (exp_close - now).total_seconds()
        t = max(time_diff / (365.25 * 86400.0), 0.0001)

        iv = calculate_implied_volatility(ltp, spot, strike, t, r, opt_type)
        if iv <= 0:
            return default_greeks

        d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t) / (iv * math.sqrt(t))
        d2 = d1 - iv * math.sqrt(t)

        npdf_d1 = _npdf(d1)
        gamma = npdf_d1 / (spot * iv * math.sqrt(t))
        vega = (spot * math.sqrt(t) * npdf_d1) / 100.0  # Change per 1% move in IV

        if opt_type == "CE":
            delta = _cnd(d1)
            theta = (- (spot * npdf_d1 * iv) / (2 * math.sqrt(t)) - r * strike * math.exp(-r * t) * _cnd(d2)) / 365.0
        else:
            delta = _cnd(d1) - 1.0
            theta = (- (spot * npdf_d1 * iv) / (2 * math.sqrt(t)) + r * strike * math.exp(-r * t) * _cnd(-d2)) / 365.0

        return {
            "iv": round(iv * 100, 2),       # Reported in percentage (e.g., 14.5%)
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 2),       # Daily rupee decay
            "vega": round(vega, 2)
        }
    except Exception:
        return default_greeks


# ---------------------------------------------------------
# State Update Handlers (Invoked by websocket_feed.py)
# ---------------------------------------------------------
def set_feed_status(status: str, error_msg: Optional[str] = None):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["server_status"] = status
        if error_msg:
            MARKET_SNAPSHOT["last_error"] = error_msg


def record_reconnect():
    with _STATE_LOCK:
        MARKET_SNAPSHOT["reconnect_count"] += 1


def update_spot(ltp: float, ts_now: str, high: float = 0.0, low: float = 0.0, net_change: float = 0.0):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["spot"]["ltp"] = ltp
        MARKET_SNAPSHOT["spot"]["high"] = high
        MARKET_SNAPSHOT["spot"]["low"] = low
        MARKET_SNAPSHOT["spot"]["net_change"] = net_change
        MARKET_SNAPSHOT["spot"]["last_trade_time"] = ts_now


def update_vix(ltp: float, net_change: float = 0.0):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["indices"]["india_vix"]["ltp"] = ltp
        MARKET_SNAPSHOT["indices"]["india_vix"]["net_change"] = net_change


def update_banknifty(ltp: float, net_change: float = 0.0):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["indices"]["banknifty"]["ltp"] = ltp
        MARKET_SNAPSHOT["indices"]["banknifty"]["net_change"] = net_change


def update_heavyweight(symbol: str, ltp: float, net_change: float, volume: int):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["heavyweights"][symbol] = {
            "ltp": ltp,
            "net_change": net_change,
            "volume": volume
        }


def update_futures(ltp: float, oi: int, volume: int, net_change: float):
    with _STATE_LOCK:
        MARKET_SNAPSHOT["futures"] = {
            "ltp": ltp,
            "open_interest": oi,
            "volume": volume,
            "net_change": net_change
        }


def update_option_quote(
    category: str,
    expiry: str,
    strike: str,
    opt_type: str,
    ltp: float,
    oi: int,
    volume: int,
    high: float,
    low: float,
    net_change: float
):
    """
    Updates option contract metrics and automatically recalculates Greeks against active Spot price.
    """
    key = f"{strike}_{opt_type}"
    sub_bucket = category.replace("options_", "")  # e.g., 'current', 'wings', 'next', 'monthly'

    # Retrieve current spot for Greeks calculation
    with _STATE_LOCK:
        current_spot = MARKET_SNAPSHOT["spot"]["ltp"]

    # Compute Greeks outside of global lock to eliminate blocking on concurrent ticks
    greeks = calculate_greeks(
        spot=current_spot,
        strike=float(strike),
        expiry_str=expiry,
        opt_type=opt_type,
        ltp=ltp
    )

    with _STATE_LOCK:
        target_dict = None
        if sub_bucket == "current":
            target_dict = MARKET_SNAPSHOT["options"]["current_expiry"]
        elif sub_bucket == "wings":
            target_dict = MARKET_SNAPSHOT["options"]["wings"]
        elif sub_bucket == "next":
            target_dict = MARKET_SNAPSHOT["options"]["next_expiry"]
        elif sub_bucket == "monthly":
            target_dict = MARKET_SNAPSHOT["options"]["monthly_expiry"]

        if target_dict is not None:
            target_dict[key] = {
                "strike": float(strike),
                "option_type": opt_type,
                "expiry": expiry,
                "ltp": ltp,
                "open_interest": oi,
                "volume": volume,
                "high": high,
                "low": low,
                "net_change": net_change,
                "greeks": greeks
            }