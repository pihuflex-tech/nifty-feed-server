import json
import urllib.request
import threading
import time
import pytest
import broadcaster

def test_snapshot_generation():
    broadcaster.init_contracts({
        "24000": {
            "CE": {"token": "1001", "symbol": "NIFTY24000CE", "ltp": 120.5},
            "PE": {"token": "1002", "symbol": "NIFTY24000PE", "ltp": 85.0}
        }
    })
    broadcaster.update_spot(24050.25, "13:30:00")
    
    snapshot = broadcaster.get_snapshot()
    assert snapshot["spot"]["ltp"] == 24050.25
    assert snapshot["spot"]["symbol"] == "NIFTY"
    assert snapshot["contracts"]["24000"]["CE"]["ltp"] == 120.5


def test_broadcaster_endpoints():
    # Start broadcaster on a test port
    test_port = 5599
    server = broadcaster.create_http_server("127.0.0.1", test_port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # 1. Test GET /
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "spot" in data
            assert "contracts" in data

        # 2. Test GET /health
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/health") as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
            assert health["status"] == "ok"

        # 3. Test GET /ready
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/ready") as resp:
            assert resp.status == 200
            ready = json.loads(resp.read().decode("utf-8"))
            assert "ready" in ready

        # 4. Test GET /metrics
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/metrics") as resp:
            assert resp.status == 200
            metrics = json.loads(resp.read().decode("utf-8"))
            assert "ticks_received" in metrics

        # 5. Test GET /expiries
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/expiries") as resp:
            assert resp.status == 200
            exp_data = json.loads(resp.read().decode("utf-8"))
            assert "available_expiries" in exp_data

        # 6. Test GET /set_expiry callback
        broadcaster.SET_EXPIRY_CALLBACK = lambda exp: (True, "Switched")
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/set_expiry?expiry=2026-10-23") as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "ok"

        # 7. Test GET /ui
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/ui") as resp:
            assert resp.status == 200
            content_type = resp.headers.get("Content-Type", "")
            assert "text/html" in content_type
            html_body = resp.read().decode("utf-8")
            assert "NIFTY" in html_body

        # 8. Test OPTIONS request (CORS Preflight)
        req = urllib.request.Request(f"http://127.0.0.1:{test_port}/", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            assert resp.headers.get("Content-Length") == "0"

        # 9. Test GET /set_expiry missing param -> 400
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{test_port}/set_expiry")
        assert exc_info.value.code == 400

        # 10. Test GET /set_expiry failure callback -> 400
        broadcaster.SET_EXPIRY_CALLBACK = lambda exp: (False, "Expiry not available")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{test_port}/set_expiry?expiry=2030-01-01")
        assert exc_info.value.code == 400

        # 11. Test 404 on unknown route
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{test_port}/unknown_route")
        assert exc_info.value.code == 404

    finally:
        server.shutdown()
        server.server_close()


def test_stale_feed_detection():
    # Simulate an active feed where the last tick occurred beyond STALE_TICK_SECONDS
    import config
    broadcaster.set_feed_status("CONNECTED")
    broadcaster.METRICS["last_tick_time"] = time.time() - (config.STALE_TICK_SECONDS + 5.0)

    # get_current_feed_status should report STALE
    assert broadcaster.get_current_feed_status() == "STALE"

    # Reset with fresh tick
    broadcaster.record_tick()
    assert broadcaster.get_current_feed_status() == "CONNECTED"

