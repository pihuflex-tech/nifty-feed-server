import time
import pytest
import websocket_feed
import broadcaster

def test_paise_to_rupee_tick_routing():
    token_map = {
        "47100": ("23100", "CE"),
        "47101": ("23100", "PE"),
    }
    broadcaster.init_contracts({
        "23100": {
            "CE": {"token": "47100", "symbol": "NIFTY23100CE", "ltp": None},
            "PE": {"token": "47101", "symbol": "NIFTY23101PE", "ltp": None},
        }
    })

    # 1. Option contract tick: 10550 paise -> 105.50 Rupees
    msg_ce = {"token": "47100", "last_traded_price": 10550}
    websocket_feed.process_tick_message(msg_ce, token_map)
    snapshot = broadcaster.get_snapshot()
    assert snapshot["contracts"]["23100"]["CE"]["ltp"] == 105.50

    # 2. Spot index tick: 2312500 paise -> 23125.00 Rupees
    msg_spot = {"token": "99926000", "last_traded_price": 2312500}
    websocket_feed.process_tick_message(msg_spot, token_map)
    snapshot = broadcaster.get_snapshot()
    assert snapshot["spot"]["ltp"] == 23125.00


def test_invalid_and_negative_prices():
    token_map = {"47100": ("23100", "CE")}
    
    # Zero price should be rejected
    broadcaster.update_contract("23100", "CE", 50.0)
    msg_zero = {"token": "47100", "last_traded_price": 0}
    websocket_feed.process_tick_message(msg_zero, token_map)
    assert broadcaster.get_snapshot()["contracts"]["23100"]["CE"]["ltp"] == 50.0

    # Negative price should be rejected
    msg_neg = {"token": "47100", "last_traded_price": -500}
    websocket_feed.process_tick_message(msg_neg, token_map)
    assert broadcaster.get_snapshot()["contracts"]["23100"]["CE"]["ltp"] == 50.0

    # Unknown token should be rejected safely
    msg_unk = {"token": "999999", "last_traded_price": 1000}
    websocket_feed.process_tick_message(msg_unk, token_map)


def test_exponential_backoff_calculation():
    # Backoff sequence: starts at 2s, grows with multiplier, caps at max_delay
    b1 = websocket_feed.calculate_backoff_delay(attempt=1, base_delay=2.0, max_delay=60.0, jitter=0.0)
    b2 = websocket_feed.calculate_backoff_delay(attempt=2, base_delay=2.0, max_delay=60.0, jitter=0.0)
    b3 = websocket_feed.calculate_backoff_delay(attempt=3, base_delay=2.0, max_delay=60.0, jitter=0.0)
    b10 = websocket_feed.calculate_backoff_delay(attempt=10, base_delay=2.0, max_delay=60.0, jitter=0.0)

    assert b1 == 2.0
    assert b2 == 4.0
    assert b3 == 8.0
    assert b10 == 60.0


def test_batch_tick_processing():
    token_map = {
        "47100": ("23100", "CE"),
        "47101": ("23100", "PE"),
    }
    broadcaster.init_contracts({
        "23100": {
            "CE": {"token": "47100", "symbol": "NIFTY23100CE", "ltp": None},
            "PE": {"token": "47101", "symbol": "NIFTY23101PE", "ltp": None},
        }
    })

    # Pass list of ticks (batch)
    batch = [
        {"token": "47100", "last_traded_price": 11000},
        {"token": "47101", "last_traded_price": 9500},
        {"token": "99926000", "last_traded_price": 2315000},
    ]
    res = websocket_feed.process_tick_message(batch, token_map)
    assert res is True

    snapshot = broadcaster.get_snapshot()
    assert snapshot["contracts"]["23100"]["CE"]["ltp"] == 110.00
    assert snapshot["contracts"]["23100"]["PE"]["ltp"] == 95.00
    assert snapshot["spot"]["ltp"] == 23150.00
