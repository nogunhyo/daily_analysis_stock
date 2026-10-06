from datetime import date, datetime

from stock_agent.ai_commentary import Commentary
from stock_agent.analysis import PULLBACK, Regime, Setup, StockAnalysis, TradePlan, TrendCheck
from stock_agent.config import Settings, StockMeta
from stock_agent.report import build_ai, build_messages, fmt_price
from stock_agent.telegram import MAX_LEN, split_message


def _analysis(ticker="000660.KS", market="KR"):
    meta = StockMeta(ticker, "SK하이닉스 <테스트>", market, "메모리·HBM", "HBM & 에이전트")
    plan = TradePlan(
        entry=100_000, stop=94_000, r=6_000, risk_pct=0.06,
        add1=106_000, add1_stop=100_000, add2=112_000, add2_stop=106_000,
        target=118_000, target_r=3, tranche_pcts=(16.7, 10.0, 6.7), risk_budget_pct=1.0,
    )
    setup = Setup(PULLBACK, 100_000, "눌림 후 반등 확인", 110_000, 0.1, 0.8)
    return StockAnalysis(
        meta, date(2026, 10, 6), 98_000, 3_000, 90, 0.05, TrendCheck(8, 8, []), setup, plan, 88,
        warnings=["실적발표 D-3"], earnings_date=date(2026, 10, 10),
    )


def test_fmt_price():
    assert fmt_price(1_773_000, "KR") == "₩1,773,000"
    assert fmt_price(239.237, "US") == "$239.24"


def test_messages_escape_html_and_fit_telegram():
    a = _analysis()
    regimes = {"US": Regime("강세", 1.0, 0.05, 0.1, date(2026, 10, 6)),
               "KR": Regime("약세", 0.5, -0.05, -0.1, date(2026, 10, 6))}
    msgs = build_messages(
        datetime(2026, 10, 7, 7, 0), regimes, {"US": "QQQ", "KR": "069500.KS"},
        [a], [a], [], Commentary("a < b & **굵게**", [("t", "https://x.com/?a=1&b=2")], "m"), Settings(),
    )
    joined = "\n".join(msgs)
    assert "&lt;테스트&gt;" in joined and "HBM &amp; 에이전트" in joined
    assert "<테스트>" not in joined
    assert "₩94,000" in joined and "₩106,000" in joined
    assert "약세장은 50%" in joined
    for m in msgs:
        assert all(len(chunk) <= MAX_LEN for chunk in split_message(m))


def test_build_ai_strips_markdown():
    out = build_ai(Commentary("## 제목\n**굵게** 본문", [], "m"))
    assert "**" not in out and "##" not in out and "굵게 본문" in out


def test_split_message_keeps_lines_and_limit():
    text = "\n".join(f"line {i} " + "가" * 50 for i in range(300))
    chunks = split_message(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_handles_overlong_line():
    chunks = split_message("x" * 2500, limit=1000)
    assert [len(c) for c in chunks] == [1000, 1000, 500]
