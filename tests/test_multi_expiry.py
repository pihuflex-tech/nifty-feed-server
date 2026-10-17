import os
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
import scrip_master
import config

def test_multi_expiry_cache_generation(tmp_path, monkeypatch):
    test_cache = tmp_path / "filtered_nifty_scrips.json"
    monkeypatch.setattr(config, "FILTERED_CACHE_FILE", str(test_cache))

    # Test filtering with mock master data
    tz = ZoneInfo("Asia/Kolkata")
    dt = datetime(2026, 10, 16, 10, 0, 0, tzinfo=tz)

    mock_scrips = []
    for exp_str in ["19OCT2026", "26OCT2026", "02NOV2026", "09NOV2026"]:
        for strike in [2400000.0, 2410000.0]:
            mock_scrips.append({
                "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",
                "expiry": exp_str, "strike": str(strike),
                "symbol": f"NIFTY{exp_str}{int(strike/100)}CE", "token": f"T_CE_{exp_str}_{strike}"
            })
            mock_scrips.append({
                "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": "NIFTY",
                "expiry": exp_str, "strike": str(strike),
                "symbol": f"NIFTY{exp_str}{int(strike/100)}PE", "token": f"T_PE_{exp_str}_{strike}"
            })

    monkeypatch.setattr(scrip_master, "load_or_download_scrips", lambda: mock_scrips)

    # 1. First call: populates cache with top 3 expiries
    contracts, t_map, tokens, exp_str, avail = scrip_master.filter_nearest_nifty_options(now_dt=dt)
    assert len(avail) == 3
    assert avail == ["2026-10-19", "2026-10-26", "2026-11-02"]
    assert exp_str == "2026-10-19"
    assert test_cache.exists()

    with open(test_cache, "r", encoding="utf-8") as f:
        cache_data = json.load(f)
    assert "expiries_data" in cache_data
    assert len(cache_data["expiries_data"]) == 3
    assert "2026-10-26" in cache_data["expiries_data"]

    # 2. Second call: instant-loads next expiry from fast-cache
    c2, t_map2, tokens2, exp_str2, avail2 = scrip_master.filter_nearest_nifty_options(
        now_dt=dt, target_expiry=date(2026, 10, 26)
    )
    assert exp_str2 == "2026-10-26"
    assert "24000" in c2
    assert "24100" in c2
    assert t_map2["T_CE_26OCT2026_2400000.0"] == ("24000", "CE")

    # 3. Third call: instant-loads 3rd expiry from fast-cache
    c3, t_map3, tokens3, exp_str3, avail3 = scrip_master.filter_nearest_nifty_options(
        now_dt=dt, target_expiry=date(2026, 11, 2)
    )
    assert exp_str3 == "2026-11-02"
    assert t_map3["T_PE_02NOV2026_2410000.0"] == ("24100", "PE")

    # 4. Target expiry passed as string
    c4, t_map4, tokens4, exp_str4, avail4 = scrip_master.filter_nearest_nifty_options(
        now_dt=dt, target_expiry="2026-10-26"
    )
    assert exp_str4 == "2026-10-26"

    # 5. Target expiry passed as datetime
    c5, t_map5, tokens5, exp_str5, avail5 = scrip_master.filter_nearest_nifty_options(
        now_dt=dt, target_expiry=datetime(2026, 10, 26, 15, 0, 0)
    )
    assert exp_str5 == "2026-10-26"

    # 6. Invalid target expiry raises ValueError
    import pytest
    with pytest.raises(ValueError) as exc:
        scrip_master.filter_nearest_nifty_options(
            now_dt=dt, target_expiry=date(2030, 1, 1)
        )
    assert "not in available active expiries" in str(exc.value)
