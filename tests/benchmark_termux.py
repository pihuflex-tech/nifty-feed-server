"""
Empirical Performance and Resource Benchmark for nifty-feed-server
Measures:
1. Startup time with fast-cache
2. Snapshot serialization time & throughput
3. Lock hold duration
4. Process memory footprint
"""

import time
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import psutil
import config

import scrip_master
import broadcaster

def run_benchmarks():
    print("==================================================================")
    print(" NIFTY FEED SERVER - RESOURCE & PERFORMANCE BENCHMARK (TERMUX OPT)")
    print("==================================================================")

    process = psutil.Process(os.getpid())
    init_mem_mb = process.memory_info().rss / (1024 * 1024)
    print(f"[*] Initial Process RAM: {init_mem_mb:.2f} MB")

    # 1. Measure Fast-Cache Load Time
    t0 = time.perf_counter()
    contracts, token_map, tokens, active_exp_str, avail_exp_strings = scrip_master.filter_nearest_nifty_options()
    t_cache = (time.perf_counter() - t0) * 1000.0
    print(f"[*] Fast-Cache Load Time: {t_cache:.2f} ms ({len(contracts)} strikes, {len(tokens)} tokens)")
    print(f"[*] Active Expiry: {active_exp_str} (Available 3: {', '.join(avail_exp_strings)})")

    post_cache_mem_mb = process.memory_info().rss / (1024 * 1024)
    print(f"[*] Post-Cache Memory: {post_cache_mem_mb:.2f} MB (Delta: +{post_cache_mem_mb - init_mem_mb:.2f} MB)")

    # 2. Populate Broadcaster State
    broadcaster.init_contracts(
        contracts,
        expiry=active_exp_str,
        available_expiries=avail_exp_strings,
    )
    broadcaster.update_spot(23150.75, "13:30:00")

    # 3. Benchmark 3-Expiry Switch Latency
    if len(avail_exp_strings) > 1:
        from datetime import datetime as dt_cls
        next_exp = avail_exp_strings[1]
        next_date = dt_cls.strptime(next_exp, "%Y-%m-%d").date()
        t0 = time.perf_counter()
        c2, t2, tok2, exp2, avail2 = scrip_master.filter_nearest_nifty_options(target_expiry=next_date)
        t_switch_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[*] 3-Expiry Switch Latency ({next_exp}): {t_switch_ms:.4f} ms")

    # 4. Benchmark Snapshot Generation & Serialization (1000 iterations)
    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        snap = broadcaster.get_snapshot()
        payload = json.dumps(snap).encode("utf-8")
    total_time_ms = (time.perf_counter() - t0) * 1000.0
    avg_per_req_ms = total_time_ms / iterations
    req_per_sec = iterations / (total_time_ms / 1000.0)

    print(f"[*] Snapshot Generation (Uncached): {avg_per_req_ms:.3f} ms / req ({req_per_sec:.0f} req/s)")
    print(f"[*] Payload Size: {len(payload)} bytes (~{len(payload)/1024.0:.1f} KB)")

    # 5. Benchmark Cached Snapshot Delivery (10000 iterations)
    iterations_cached = 10000
    t0 = time.perf_counter()
    for _ in range(iterations_cached):
        payload_cached = broadcaster.get_snapshot_bytes()
    total_cached_time_ms = (time.perf_counter() - t0) * 1000.0
    avg_cached_ms = total_cached_time_ms / iterations_cached
    cached_req_per_sec = iterations_cached / (total_cached_time_ms / 1000.0)

    print(f"[*] Snapshot Delivery (Dirty-Flag Cached): {avg_cached_ms:.4f} ms / req ({cached_req_per_sec:.0f} req/s)")
    print(f"[*] CPU Throughput Speedup: {avg_per_req_ms / avg_cached_ms:.1f}x faster")


    final_mem_mb = process.memory_info().rss / (1024 * 1024)
    print(f"[*] Final Steady RAM: {final_mem_mb:.2f} MB")
    print("==================================================================")

if __name__ == "__main__":
    run_benchmarks()
