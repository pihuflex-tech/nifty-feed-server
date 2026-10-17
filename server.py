#!/usr/bin/env python3
"""
NIFTY LIVE FEED SERVER (Producer Entrypoint)
Decoupled WebSocket Client & High-Performance HTTP JSON Broadcaster
Supports dynamic multi-expiry selection and Termux optimization.
"""

import os
import sys
import signal
import threading
from datetime import datetime

# Lock working directory to script folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config
import security
import scrip_master
import broadcaster
import websocket_feed


def main() -> None:
    # 1. Initialize safe logging before any broker interaction
    security.configure_safe_logging()

    print("==========================================================================")
    print(f" NIFTY LIVE FEED SERVER | http://{config.FEED_HOST}:{config.FEED_PORT}/")
    print("==========================================================================")

    # 2. Validate environment credentials
    is_valid, missing_credentials = config.validate_credentials()
    if not is_valid:
        print(f"[!] Configuration Error: Missing required credentials in .env: {', '.join(missing_credentials)}")
        print("[!] Please update your .env file with valid Angel One SmartAPI credentials.")
        sys.exit(1)

    # 3. Initialize HTTP Broadcaster with port collision check
    try:
        server_instance = broadcaster.create_http_server(config.FEED_HOST, config.FEED_PORT)
        broadcaster.ACTIVE_SERVER = server_instance
        http_thread = threading.Thread(target=server_instance.serve_forever, daemon=True)
        http_thread.start()
        print(f"[*] HTTP Broadcaster active at http://{config.FEED_HOST}:{config.FEED_PORT}/")
    except OSError as e:
        print(f"[!] Network Error: Unable to bind to {config.FEED_HOST}:{config.FEED_PORT} ({e}).")
        print("[!] Check if another instance of nifty-feed-server is already running.")
        sys.exit(1)

    # Signal handlers for graceful shutdown
    def handle_shutdown(signum, frame):
        print("\n[*] Initiating graceful shutdown...")
        broadcaster.stop_http_server()
        print("[*] Server stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    # 4. Extract and pre-populate NIFTY nearest-expiry option chain
    try:
        contracts_template, token_map, subscription_tokens, active_exp_str, avail_exp_strings = (
            scrip_master.filter_nearest_nifty_options()
        )
        broadcaster.init_contracts(
            contracts_template,
            expiry=active_exp_str,
            available_expiries=avail_exp_strings,
        )
    except Exception as e:
        print(f"[!] Instrument Master Error: {security.redact_string(str(e))}")
        broadcaster.stop_http_server()
        sys.exit(1)

    # Register dynamic expiry switch handler for HTTP clients and dashboard
    def handle_expiry_switch(new_expiry_str: str):
        try:
            target_date = datetime.strptime(new_expiry_str.strip(), "%Y-%m-%d").date()
            c_tmpl, t_map, s_tokens, exp_str, avail_exp = scrip_master.filter_nearest_nifty_options(
                target_expiry=target_date
            )
            if exp_str != str(target_date):
                return False, f"Requested expiry {target_date} is not active. Available expiries: {', '.join(avail_exp)}"
            broadcaster.init_contracts(
                c_tmpl,
                expiry=exp_str,
                available_expiries=avail_exp,
            )
            websocket_feed.update_subscriptions(s_tokens, t_map)
            print(f"[*] Successfully switched to expiry {exp_str} ({len(c_tmpl)} strikes).")
            return True, f"Switched to expiry {exp_str}"
        except Exception as err:
            return False, str(err)

    broadcaster.SET_EXPIRY_CALLBACK = handle_expiry_switch

    # 5. Authenticate with Angel One & seed initial Spot LTP
    try:
        jwt_token, feed_token = websocket_feed.generate_session()
    except Exception as e:
        print(f"[!] Authentication Error: {security.redact_string(str(e))}")
        broadcaster.stop_http_server()
        sys.exit(1)

    # 6. Start WebSocket 2.0 stream (blocks main thread with auto-reconnect)
    try:
        websocket_feed.start_stream(jwt_token, feed_token, subscription_tokens, token_map)
    except KeyboardInterrupt:
        handle_shutdown(None, None)


if __name__ == "__main__":
    main()