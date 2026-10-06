import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_agent.analysis import (
    BREAKOUT,
    EXTENDED,
    PIVOT,
    StockAnalysis,
    TrendCheck,
    add_indicators,
    apply_earnings,
    build_plan,
    detect_setup,
    krx_tick,
    round_price,
    select_picks,
    trend_template,
)
from stock_agent.config import Settings, StockMeta


def make_df(closes, spread=0.01, volume=1_000_000):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * (1 + spread),
            "Low": closes * (1 - spread),
            "Close": closes,
            "Volume": np.full(len(closes), volume, dtype=float),
        },
        index=idx,
    )


def uptrend(n=300, start=100.0, daily=0.003):
    return start * (1 + daily) ** np.arange(n)


def test_krx_tick_table():
    assert krx_tick(1_999) == 1
    assert krx_tick(4_999) == 5
    assert krx_tick(19_990) == 10
    assert krx_tick(49_950) == 50
    assert krx_tick(199_900) == 100
    assert krx_tick(499_500) == 500
    assert krx_tick(500_000) == 1_000


def test_round_price_respects_tick_and_direction():
    assert round_price(278_340, "KR", "down") == 278_000
    assert round_price(278_340, "KR", "up") == 278_500
    assert round_price(101.234, "US", "up") == 101.24
    assert round_price(101.236, "US", "down") == 101.23


def test_trend_template_steady_uptrend_passes_all():
    df = add_indicators(make_df(uptrend()))
    check = trend_template(df, rs_rank=90)
    assert check.points == 8 and check.stage == "상승추세"


def test_trend_template_downtrend_fails():
    df = add_indicators(make_df(uptrend(daily=-0.003)))
    check = trend_template(df, rs_rank=10)
    assert check.points <= 2 and check.stage == "약세"


def test_plan_stop_between_1_and_2_atr_and_pyramid_levels():
    s = Settings()
    df = add_indicators(make_df(uptrend()))
    atr = df["atr"].iloc[-1]
    entry = df["Close"].iloc[-1]
    plan = build_plan(df, entry, "US", s)

    assert plan.entry - 2 * atr - 0.01 <= plan.stop <= plan.entry - atr + 0.01
    assert plan.r == pytest.approx(plan.entry - plan.stop)
    assert plan.add1 == pytest.approx(plan.entry + plan.r, abs=0.011)
    assert plan.add2 == pytest.approx(plan.entry + 2 * plan.r, abs=0.011)
    assert plan.target == pytest.approx(plan.entry + 3 * plan.r, abs=0.011)
    assert plan.add1_stop == plan.entry  # 2차 추가 시 손절을 본전으로
    assert plan.add2_stop == plan.add1  # 3차 추가 시 손절을 +1R로


def test_plan_position_size_limits_loss_to_risk_budget():
    s = Settings(risk_per_trade_pct=1.0, max_first_tranche_pct=100)
    df = add_indicators(make_df(uptrend()))
    plan = build_plan(df, df["Close"].iloc[-1], "US", s)
    first, second, third = plan.tranche_pcts
    # 1차 비중 × 손절폭 = 계좌 위험 1%
    assert first * plan.risk_pct == pytest.approx(1.0)
    assert second / first == pytest.approx(30 / 50)
    assert third / first == pytest.approx(20 / 50)


def test_plan_first_tranche_cap_and_regime_multiplier():
    s = Settings(max_first_tranche_pct=5)
    df = add_indicators(make_df(uptrend()))
    plan = build_plan(df, df["Close"].iloc[-1], "US", s, risk_mult=0.5)
    assert plan.tranche_pcts[0] <= 5
    assert plan.risk_budget_pct == 0.5


def test_plan_kr_prices_on_tick_grid():
    s = Settings()
    df = add_indicators(make_df(uptrend(start=150_000)))
    plan = build_plan(df, df["Close"].iloc[-1], "KR", s)
    for price in (plan.entry, plan.stop, plan.add1, plan.add2, plan.target):
        assert price % krx_tick(price) == 0


def test_detect_breakout_with_volume():
    s = Settings()
    closes = list(uptrend(280))
    base = closes[-1]
    closes += [base * 0.98] * 15  # 횡보(베이스)
    closes += [base * 1.01]  # 직전 고가 돌파
    df = make_df(closes, spread=0.005)
    df.iloc[-1, df.columns.get_loc("Volume")] = 3_000_000
    setup = detect_setup(add_indicators(df), s)
    assert setup.kind == BREAKOUT
    assert setup.volume_confirmed


def test_detect_near_pivot():
    s = Settings()
    closes = list(uptrend(280))
    base = closes[-1]
    closes += [base * 0.97] * 15
    df = make_df(closes, spread=0.005)
    setup = detect_setup(add_indicators(df), s)
    assert setup.kind == PIVOT
    assert setup.entry > setup.pivot


def test_detect_extended_after_spike():
    s = Settings()
    closes = list(uptrend(280)) + list(uptrend(10, start=uptrend(280)[-1] * 1.02, daily=0.04))
    df = add_indicators(make_df(closes, spread=0.005))
    setup = detect_setup(df, s)
    assert setup.kind == EXTENDED
    assert setup.entry < df["Close"].iloc[-1]


def _stub(ticker, layer, score):
    meta = StockMeta(ticker, ticker, "US", layer, "")
    return StockAnalysis(meta, date(2026, 1, 1), 1, 1, 50, 0, TrendCheck(8, 8, []), None, None, score)


def test_select_picks_diversifies_by_layer():
    cands = [
        _stub("A", "네트워크·스위치", 90),
        _stub("B", "네트워크·광", 88),
        _stub("C", "메모리·HBM", 80),
        _stub("D", "전력·냉각", 70),
    ]
    picks = select_picks(cands, top_n=3, max_per_layer=1)
    assert [p.meta.ticker for p in picks] == ["A", "C", "D"]


def test_earnings_penalty_only_when_imminent():
    s = Settings(earnings_warn_days=7)
    today = date(2026, 10, 7)
    near, far = _stub("A", "x", 80), _stub("B", "x", 80)
    apply_earnings(near, date(2026, 10, 10), today, s)
    apply_earnings(far, date(2026, 11, 20), today, s)
    assert near.score == 70 and any("실적발표" in w for w in near.warnings)
    assert far.score == 80 and not far.warnings
    assert not math.isnan(near.score)


def test_pyramid_worst_case_after_adds():
    """2차 후 손절 시 손실 < 1차 위험의 60%, 3차 후 손절 시 전체 손익 ≥ 0 (갭 없음 가정)."""
    s = Settings(max_first_tranche_pct=100)
    df = add_indicators(make_df(uptrend()))
    p = build_plan(df, df["Close"].iloc[-1], "US", s)
    v1, v2, v3 = p.tranche_pcts
    shares = (v1 / p.entry, v2 / p.add1, v3 / p.add2)

    first_risk = shares[0] * (p.entry - p.stop)
    after_add1 = shares[0] * (p.add1_stop - p.entry) + shares[1] * (p.add1_stop - p.add1)
    after_add2 = sum(n * (p.add2_stop - px) for n, px in zip(shares, (p.entry, p.add1, p.add2)))
    assert -after_add1 < 0.6 * first_risk
    assert after_add2 >= 0
