"""
server.py
Main entry point for Nifty Feed Server (Mode 2 Quote Engine).
Authenticates, seeds Spot via REST, filters 92 curated tokens around actual ATM,
and starts streaming over WebSocket and ZeroMQ PUB (Port 5555).
"""

import os
import json
import scrip_master
import websocket_feed
import broadcaster

CONFIG_FILE = "filtered_nifty_scrips.json"


def prepare_tokens(real_spot: float) -> dict:
    """Filters 92 curated tokens centered dynamically on the live spot price."""
    print(f"[2/3] Filtering curated 92 tokens around Live Spot: ₹{real_spot}...")
    scrips = scrip_master.fetch_scrip_master()
    config_data = scrip_master.build_filtered_tokens(scrips, estimated_spot=real_spot)

    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=2)

    print(f"[+] Total Active Tokens Configured: {config_data['total_tokens']}/92")
    return config_data


def main():
    print("=" * 55)
    print("  NIFTY FEED SERVER - MODE 2 QUOTE ENGINE (LIVE SPOT)")
    print("=" * 55)

    # 1. Session Generation & Initial Spot Seed
    print("[1/3] Authenticating with Angel One...")
    jwt_token, feed_token, seeded_spot = websocket_feed.generate_session()

    # Fallback reference spot if initial REST fetch returned 0
    spot_to_use = seeded_spot if seeded_spot > 0 else 22800.0

    # 2. Scrip Master Token Filter around exact ATM
    token_config = prepare_tokens(real_spot=spot_to_use)
    tokens_by_seg = token_config["tokens_by_seg"]
    token_meta = token_config["instruments"]

    # 3. Launch WebSocket Streaming & ZMQ Broadcast Engine (Port 5555)
    print("[3/3] Launching Mode 2 WebSocket Stream & ZMQ PUB Engine...")
    websocket_feed.start_stream(
        jwt_token=jwt_token,
        feed_token=feed_token,
        tokens_by_seg=tokens_by_seg,
        token_metadata=token_meta,
        chunk_size=50,
        chunk_interval=0.05
    )


if __name__ == "__main__":
    main()