import os
import json
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
import scrip_master
import config

MOCK_NIFTY_SCRIPS = [
    # Contracts expiring today (2026-10-16)
    {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "16OCT2026", "strike": "2400000.0", "symbol": "NIFTY16OCT2624000CE", "token": "1001"},
    {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "16OCT2026", "strike": "2400000.0", "symbol": "NIFTY16OCT2624000PE", "token": "1002"},
    # Contracts expiring next week (2026-10-23)
    {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "23OCT2026", "strike": "2400000.0", "symbol": "NIFTY23OCT2624000CE", "token": "2001"},
    {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": "23OCT2026", "strike": "2400000.0", "symbol": "NIFTY23OCT2624000PE", "token": "2002"},
    # Non-Nifty / Non-OPTIDX
    {"exch_seg": "NSE", "instrumenttype": "AMXIDX", "name": "NIFTY", "expiry": "", "strike": "0", "symbol": "Nifty 50", "token": "99926000"},
    {"exch_seg": "NFO", "instrumenttype": "OPTSTK", "name": "RELIANCE", "expiry": "23OCT2026", "strike": "300000.0", "symbol": "RELIANCE23OCT3000CE", "token": "3001"},
]

def test_expiry_selection_before_cutoff():
    # 2026-10-16 at 14:00 (Before 15:30 cutoff)
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 16, 14, 0, 0, tzinfo=tz)
    
    contracts, token_map, tokens, expiry, all_exp = scrip_master.filter_instruments_data(
        MOCK_NIFTY_SCRIPS, now_dt=dt
    )
    assert expiry == date(2026, 10, 16)
    assert len(all_exp) == 2
    assert "24000" in contracts
    assert token_map["1001"] == ("24000", "CE")
    assert token_map["1002"] == ("24000", "PE")


def test_expiry_selection_after_cutoff():
    # 2026-10-16 at 16:00 (After 15:30 cutoff on expiry day)
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 16, 16, 0, 0, tzinfo=tz)
    
    contracts, token_map, tokens, expiry, all_exp = scrip_master.filter_instruments_data(
        MOCK_NIFTY_SCRIPS, now_dt=dt
    )
    # Must roll over to next week (2026-10-23)
    assert expiry == date(2026, 10, 23)
    assert token_map["2001"] == ("24000", "CE")
    assert token_map["2002"] == ("24000", "PE")


def test_target_expiry_selection():
    # Select next week explicitly
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=tz)
    
    contracts, token_map, tokens, expiry, all_exp = scrip_master.filter_instruments_data(
        MOCK_NIFTY_SCRIPS, now_dt=dt, target_expiry=date(2026, 10, 23)
    )
    assert expiry == date(2026, 10, 23)
    assert token_map["2001"] == ("24000", "CE")



def test_atomic_cache_write(tmp_path):
    cache_file = tmp_path / "test_cache.json"
    data = {"test_key": "test_value"}
    scrip_master.save_cache_atomic(str(cache_file), data)
    
    assert cache_file.exists()
    with open(cache_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_missing_expiry_raises_error():
    # Only past contracts
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 25, 12, 0, 0, tzinfo=tz)
    import pytest
    with pytest.raises(RuntimeError) as exc_info:
        scrip_master.filter_instruments_data(MOCK_NIFTY_SCRIPS, now_dt=dt)
    assert "No active NIFTY options found" in str(exc_info.value)


def test_top_three_expiries_filtered():
    # Mock scrips with 5 future expiries
    scrips = [
        {"exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY", "expiry": exp, "strike": "2400000.0", "symbol": f"NIFTY{exp}24000CE", "token": str(i)}
        for i, exp in enumerate(["19OCT2026", "26OCT2026", "02NOV2026", "09NOV2026", "16NOV2026"], start=5000)
    ]
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=tz)

    contracts, token_map, tokens, expiry, all_exp, expiries_cache = scrip_master.filter_instruments_data(
        scrips, now_dt=dt, return_all_cache=True
    )

    # Exactly 3 expiries kept
    assert len(all_exp) == 3
    assert all_exp[0] == date(2026, 10, 19)
    assert all_exp[1] == date(2026, 10, 26)
    assert all_exp[2] == date(2026, 11, 2)

    # All 3 expiries have pre-computed strike data in cache
    assert "2026-10-19" in expiries_cache
    assert "2026-10-26" in expiries_cache
    assert "2026-11-02" in expiries_cache
    assert "24000" in expiries_cache["2026-10-26"]["contracts"]


