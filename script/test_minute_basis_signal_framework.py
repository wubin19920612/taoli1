import pandas as pd

from minute_basis_signal_framework import MinuteSignalEngine, build_features


def synthetic_frame() -> pd.DataFrame:
    start = pd.Timestamp("2026-07-24 00:00", tz="Asia/Shanghai")
    rows = []
    for index in range(100):
        basis = 10.0
        premium = 0.0
        if index in (70, 71):
            basis, premium = 220.0, -0.01
        elif index == 72:
            basis, premium = 30.0, -0.01
        elif index == 80:
            basis, premium = 400.0, -0.01
        spot = 1.0
        futures = spot * (1 - basis / 10000)
        time = start + pd.Timedelta(minutes=index)
        rows.append(
            {
                "open_time": int(time.timestamp() * 1000),
                "time_utc": time.tz_convert("UTC"),
                "time_cst": time,
                "spot_open": spot,
                "spot_close": spot,
                "fut_open": futures,
                "fut_close": futures,
                "premium_open": premium,
                "premium_high": premium,
                "premium_low": premium,
                "premium_close": premium,
            }
        )
    return build_features(pd.DataFrame(rows))


def test_signal_only_engine_detects_shock_entry_and_take_profit():
    events = MinuteSignalEngine().scan(synthetic_frame())
    assert list(events["event_type"]) == ["SHOCK_ALERT", "ENTRY", "TAKE_PROFIT"]
    assert events.iloc[1]["planned_execution_time_cst"] == "2026-07-24 01:13"
    assert events.iloc[2]["signal_basis_gain_bps"] >= 350


def test_scan_sorts_out_of_order_input():
    frame = synthetic_frame()
    events = MinuteSignalEngine().scan(pd.concat([frame.iloc[70:], frame.iloc[:70]]))
    assert not events.empty
