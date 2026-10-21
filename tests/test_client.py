"""
test_client.py
Independent listener to verify Port 5555 Mode 2 data integrity.
"""

import zmq
import json
import time

def run_test():
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.connect("tcp://127.0.0.1:5555")
    subscriber.setsockopt(zmq.SUBSCRIBE, b"NIFTY_MARKET_TICK")

    print("[*] Listening on tcp://127.0.0.1:5555 for Mode 2 Ticks...")
    print("[*] Press Ctrl+C to stop.\n")

    tick_count = 0
    try:
        while True:
            topic, msg = subscriber.recv_multipart()
            data = json.loads(msg.decode("utf-8"))
            tick_count += 1

            spot = data.get("spot", {})
            vix = data.get("indices", {}).get("india_vix", {})
            current_opts = data.get("options", {}).get("current_expiry", {})

            # Terminal पर साफ़ आउटपुट दिखाएँ
            print(f"\r[Tick #{tick_count}] "
                  f"Spot: ₹{spot.get('ltp', 0.0)} | "
                  f"VIX: {vix.get('ltp', 0.0)} | "
                  f"Active Contracts Cached: {len(current_opts)}", end="")

            # हर 5वें टिक पर एक ऑप्शन का गहरा डेटा प्रिंट करें
            if tick_count % 5 == 0 and current_opts:
                sample_key = next(iter(current_opts))
                contract = current_opts[sample_key]
                greeks = contract.get("greeks", {})
                print(f"\n--- [Sample Mode 2 Quote: {sample_key}] ---")
                print(f"  LTP: ₹{contract.get('ltp')} | Vol: {contract.get('volume')} | OI: {contract.get('open_interest')}")
                print(f"  High: ₹{contract.get('high')} | Low: ₹{contract.get('low')}")
                print(f"  Greeks -> IV: {greeks.get('iv')}% | Delta: {greeks.get('delta')} | Theta: {greeks.get('theta')}")
                print("-" * 45)

    except KeyboardInterrupt:
        print("\n[*] Test stopped cleanly.")

if __name__ == "__main__":
    run_test()