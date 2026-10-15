#!/usr/bin/env python3
"""
NIFTY LIVE FEED SERVER (Producer Entrypoint)
Decoupled WebSocket Client & High-Performance HTTP JSON Broadcaster
"""

import os
import sys
import threading

# Lock working directory to script folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config
import scrip_master
import broadcaster
import websocket_feed

def main():
    print("==========================================================================")
    print(f" NIFTY LIVE FEED SERVER | Endpoint: http://{config.FEED_HOST}:{config.FEED_PORT}/")
    print("==========================================================================")

    # 1. Start Threaded HTTP Broadcaster
    http_thread = threading.Thread(target=broadcaster.start_http_server, daemon=True)
    http_thread.start()
    print(f"[*] HTTP Broadcaster active at http://{config.FEED_HOST}:{config.FEED_PORT}/")

    # 2. Extract and pre-populate NIFTY nearest-expiry option chain
    try:
        contracts_template, token_map, subscription_tokens = scrip_master.filter_nearest_nifty_options()
        broadcaster.init_contracts(contracts_template)
    except Exception as e:
        print(f"[!] Instrument Master Error: {e}")
        sys.exit(1)

    # 3. Authenticate with Angel One & seed initial Spot LTP
    try:
        jwt_token, feed_token = websocket_feed.generate_session()
    except Exception as e:
        print(f"[!] Authentication Error: {e}")
        sys.exit(1)

    # 4. Start WebSocket 2.0 stream (blocks main thread with auto-reconnect)
    try:
        websocket_feed.start_stream(jwt_token, feed_token, subscription_tokens, token_map)
    except KeyboardInterrupt:
        print("\n[*] Feed server stopped cleanly.")

if __name__ == "__main__":
    main()