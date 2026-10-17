import threading
import time
import random
import broadcaster

def test_concurrent_reads_and_writes():
    """
    Stress-tests state concurrency: multiple writer threads updating ticks
    simultaneously while multiple reader threads fetch snapshots.
    Verifies that no lock contention crashes, race conditions, or partial states occur.
    """
    broadcaster.init_contracts({
        str(strike): {
            "CE": {"token": f"CE_{strike}", "symbol": f"NIFTY{strike}CE", "ltp": 100.0},
            "PE": {"token": f"PE_{strike}", "symbol": f"NIFTY{strike}PE", "ltp": 100.0},
        }
        for strike in range(23000, 23500, 50)
    })

    stop_event = threading.Event()
    errors = []

    def writer_task():
        try:
            strikes = [str(s) for s in range(23000, 23500, 50)]
            while not stop_event.is_set():
                s = random.choice(strikes)
                opt = random.choice(["CE", "PE"])
                ltp = round(random.uniform(10.0, 500.0), 2)
                broadcaster.update_contract(s, opt, ltp)
                broadcaster.update_spot(round(random.uniform(23100.0, 23300.0), 2), "12:00:00")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def reader_task():
        try:
            while not stop_event.is_set():
                snap = broadcaster.get_snapshot()
                assert "spot" in snap
                assert "contracts" in snap
                assert isinstance(snap["spot"]["ltp"], float)
                time.sleep(0.002)
        except Exception as e:
            errors.append(e)

    writers = [threading.Thread(target=writer_task, daemon=True) for _ in range(4)]
    readers = [threading.Thread(target=reader_task, daemon=True) for _ in range(6)]

    for t in writers + readers:
        t.start()

    time.sleep(0.5)
    stop_event.set()

    for t in writers + readers:
        t.join(timeout=1.0)

    assert len(errors) == 0, f"Concurrent errors occurred: {errors}"
