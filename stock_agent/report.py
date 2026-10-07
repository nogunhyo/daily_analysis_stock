"""텔레그램용 리포트 메시지(HTML) 생성."""
from __future__ import annotations

import math
import re
from datetime import datetime
from html import escape as _escape

from .ai_commentary import Commentary
from .analysis import BREAKOUT, PIVOT, PULLBACK, Regime, StockAnalysis
from .config import Settings


def escape(text: str, quote: bool = False) -> str:
    """텔레그램 HTML 모드용 이스케이프 (본문에서는 따옴표를 그대로 둠)."""
    return _escape(text, quote=quote)


WEEKDAYS = "월화수목금토일"
FLAGS = {"US": "🇺🇸", "KR": "🇰🇷"}
MEDALS = ["🥇", "🥈", "🥉"]


def fmt_price(value: float, market: str) -> str:
    return f"₩{value:,.0f}" if market == "KR" else f"${value:,.2f}"


def fmt_pct(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "N/A"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def fmt_ratio(x: float) -> str:
    return "N/A" if x is None or math.isnan(x) else f"{x:.1f}배"


def _header(now: datetime, regimes: dict[str, Regime], benchmarks: dict[str, str]) -> list[str]:
    lines = [
        "<b>📈 AI 에이전트 병목·해자 데일리</b>",
        f"{now:%Y-%m-%d} ({WEEKDAYS[now.weekday()]}) {now:%H:%M} KST",
        "",
        "<b>시장 상태</b> (추세 필터)",
    ]
    for market, r in regimes.items():
        lines.append(
            f"{FLAGS[market]} {escape(benchmarks[market])} {r.last_date:%m/%d}: <b>{r.label}</b>"
            f" · 50일선 {fmt_pct(r.vs_sma50)} · 200일선 {fmt_pct(r.vs_sma200)}"
        )
    if any(r.label != "강세" for r in regimes.values()):
        lines.append("→ 중립장은 1회 위험 75%, 약세장은 50%로 자동 축소")
    return lines


def build_summary(now, regimes, benchmarks, picks: list[StockAnalysis], skipped: list[str]) -> str:
    lines = _header(now, regimes, benchmarks)
    lines += ["", f"<b>오늘의 추천 ({len(picks)})</b>"]
    if not picks:
        lines.append("신규 진입 조건(상승추세 + 돌파/눌림 자리)을 만족한 종목 없음 → <b>관망</b>")
        lines.append("억지로 자리를 만들지 않는 것도 손익비 관리입니다.")
    for i, a in enumerate(picks):
        lines.append(
            f"{i + 1}. {FLAGS[a.meta.market]} <b>{escape(a.meta.name)}</b> ({escape(a.meta.ticker)})"
            f" · {escape(a.meta.layer)} · {a.score:.0f}점 · {a.setup.kind}"
        )
    if skipped:
        lines += ["", f"⚠️ 데이터 문제로 제외: {escape(', '.join(skipped))}"]
    return "\n".join(lines)


def _entry_label(a: StockAnalysis) -> str:
    return {
        BREAKOUT: "현재가 부근 매수",
        PIVOT: "피벗 돌파 시 (스톱 매수)",
        PULLBACK: "최근 고가 돌파 시 (반등 확인)",
    }.get(a.setup.kind, "지정가 대기")


def build_pick(rank: int, a: StockAnalysis) -> str:
    m, p, mk = a.meta, a.plan, a.meta.market
    medal = MEDALS[rank] if rank < len(MEDALS) else f"{rank + 1}."
    pct = lambda price: fmt_pct(price / p.entry - 1)  # noqa: E731
    t1, t2, t3 = p.tranche_pcts

    lines = [
        f"{medal} <b>{escape(m.name)}</b> ({escape(m.ticker)}) {FLAGS[mk]}",
        f"<i>{escape(m.layer)}</i>",
        f"💡 {escape(m.thesis)}",
        "",
        f"종가 {fmt_price(a.close, mk)} ({a.last_date:%m/%d}) · 점수 {a.score:.0f}",
        f"추세 {a.trend.points}/{a.trend.total} ({a.trend.stage}) · RS {a.rs_rank:.0f}"
        f" · 지수 대비 3개월 {fmt_pct(a.excess_63d)}",
        f"셋업: <b>{a.setup.kind}</b> — {escape(a.setup.entry_rule)}",
        f"피벗 {fmt_price(a.setup.pivot, mk)} · ATR {fmt_price(a.atr, mk)} ({a.atr / a.close * 100:.1f}%)"
        f" · 거래량 {fmt_ratio(a.setup.vol_ratio)}",
        "",
        f"▶️ <b>진입</b> {fmt_price(p.entry, mk)} — {_entry_label(a)}",
        f"⛔ <b>손절</b> {fmt_price(p.stop, mk)} ({pct(p.stop)}) · 1R = {fmt_price(p.r, mk)}",
        "➕ <b>피라미딩</b>",
        f"   2차 {fmt_price(p.add1, mk)} ({pct(p.add1)}, +1R) → 전체 손절을 {fmt_price(p.add1_stop, mk)}(본전)로",
        f"   3차 {fmt_price(p.add2, mk)} ({pct(p.add2)}, +2R) → 전체 손절을 {fmt_price(p.add2_stop, mk)}(+1R)로",
        f"🎯 <b>목표</b> {fmt_price(p.target, mk)} ({pct(p.target)}, +{p.target_r:g}R) 도달 시 1/3 익절,"
        " 나머지는 '최근 고점 − 3ATR' 추적손절",
        f"💰 <b>비중</b>(계좌 {p.risk_budget_pct:g}% 위험 기준) 1차 {t1:.1f}% / 2차 {t2:.1f}% / 3차 {t3:.1f}%",
    ]

    f = a.fundamentals
    if f:
        pe = f.get("forward_pe")
        lines.append(
            f"📊 매출성장 {fmt_pct(f.get('revenue_growth'))} · 영업이익률 {fmt_pct(f.get('operating_margin'), False)}"
            f" · 선행PER {f'{pe:.1f}' if pe else 'N/A'} <i>(야후, 참고용)</i>"
        )
    if a.earnings_date:
        lines.append(f"📅 다음 실적발표 {a.earnings_date:%Y-%m-%d}")
    for w in a.warnings:
        lines.append(f"⚠️ {escape(w)}")
    return "\n".join(lines)


_MD = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"^#{1,6}\s*", re.M), "")]


def strip_markdown(text: str) -> str:
    """텔레그램에서 그대로 보이는 마크다운 기호(**굵게**, ## 제목) 제거."""
    for pattern, repl in _MD:
        text = pattern.sub(repl, text)
    return text


def build_text_message(title: str, text: str) -> str:
    """일반 텍스트(AI가 쓴 글 등)를 텔레그램 HTML 메시지로. 본문은 전부 이스케이프."""
    return f"<b>{escape(title)}</b>\n{escape(strip_markdown(text))}"


def build_ai(c: Commentary) -> str:
    lines = ["<b>🤖 AI 뉴스·해자 점검</b>", escape(strip_markdown(c.text))]
    if c.sources:
        lines += ["", "<b>출처</b>"]
        for title, url in c.sources:
            lines.append(f'• <a href="{escape(url, quote=True)}">{escape(title[:60])}</a>')
    lines.append(f"<i>({escape(c.model)} 작성 · 오류 가능, 원문 확인 권장)</i>")
    return "\n".join(lines)


def build_watchlist(analyses: list[StockAnalysis], picks: list[StockAnalysis]) -> str:
    pick_set = {a.meta.ticker for a in picks}
    lines = ["<b>📋 전체 유니버스 (점수순)</b>", "<code>종목 | 점수 | 추세 | 셋업 | RS</code>"]
    for a in analyses:
        mark = "★" if a.meta.ticker in pick_set else "·"
        lines.append(
            f"{mark} {escape(a.meta.name)} | {a.score:.0f} | {a.trend.points}/8 | {a.setup.kind} | {a.rs_rank:.0f}"
        )
    return "\n".join(lines)


def build_glossary(s: Settings) -> str:
    return f"""<b>📖 용어</b>
• <b>ATR</b>: 최근 {s.atr_period}일 '하루 평균 움직임 폭'. 손절을 이보다 좁게 잡으면 일상적 흔들림에 털림
• <b>R</b>: 진입가−손절가 = 1회 감수 손실. +{s.target_r:g}R은 위험의 {s.target_r:g}배 수익(손익비 {s.target_r:g}:1)
• <b>피벗</b>: 최근 {s.pivot_lookback}거래일 최고가. 넘으면 매물대를 뚫은 '돌파'
• <b>추세 n/8</b>: 미너비니 트렌드 템플릿 8개 조건 충족 수 (7 이상만 추천)
• <b>RS</b>: 상대강도. 이 유니버스 종목 중 최근 1년 수익률 순위(0~100)
• <b>피라미딩</b>: 수익이 날 때만 추가매수하고, 그때마다 손절을 올려 전체 위험을 줄이는 방식

<b>손절 규칙</b>: 최근 {s.stop_lookback}일 저점 아래와 {s.max_stop_atr:g}ATR 중 가까운 쪽 (단, 최소 {s.min_stop_atr:g}ATR)
<b>비중 규칙</b>: 1차 매수분이 손절되면 계좌의 {s.risk_per_trade_pct:g}%만 잃도록 계산 (상한 {s.max_first_tranche_pct:g}%)

<i>규칙 기반 자동 계산이며 투자 권유가 아닙니다. 과거 성과 검증(백테스트)을 거치지 않았고, 야후 파이낸스 데이터는 지연·오류가 있을 수 있습니다. 최종 판단과 책임은 본인에게 있습니다.</i>"""


def build_messages(
    now: datetime,
    regimes: dict[str, Regime],
    benchmarks: dict[str, str],
    analyses: list[StockAnalysis],
    picks: list[StockAnalysis],
    skipped: list[str],
    commentary: Commentary | None,
    settings: Settings,
) -> list[str]:
    messages = [build_summary(now, regimes, benchmarks, picks, skipped)]
    messages += [build_pick(i, a) for i, a in enumerate(picks)]
    if commentary:
        messages.append(build_ai(commentary))
    messages.append(build_watchlist(analyses, picks))
    messages.append(build_glossary(settings))
    return messages


def _plan_dict(p) -> dict | None:
    if p is None:
        return None
    return {
        "entry": p.entry, "stop": p.stop, "r": round(p.r, 4), "risk_pct": round(p.risk_pct * 100, 2),
        "add1": p.add1, "add1_stop": p.add1_stop, "add2": p.add2, "add2_stop": p.add2_stop,
        "target": p.target, "target_r": p.target_r,
        "tranche_pcts": [round(x, 2) for x in p.tranche_pcts], "risk_budget_pct": p.risk_budget_pct,
    }


def _analysis_dict(a: StockAnalysis, detail: bool) -> dict:
    d = {
        "ticker": a.meta.ticker, "name": a.meta.name, "market": a.meta.market, "layer": a.meta.layer,
        "score": a.score, "setup": a.setup.kind, "trend_points": a.trend.points, "rs_rank": a.rs_rank,
        "close": a.close, "last_date": a.last_date.isoformat(),
    }
    if detail:
        d.update(
            thesis=a.meta.thesis, entry_rule=a.setup.entry_rule, pivot=a.setup.pivot,
            atr=round(a.atr, 4), excess_63d_pct=round(a.excess_63d * 100, 2), trend_failed=a.trend.failed,
            plan=_plan_dict(a.plan), warnings=a.warnings,
            earnings_date=a.earnings_date.isoformat() if a.earnings_date else None,
            fundamentals=a.fundamentals,
        )
    return d


def build_snapshot(now, regimes, analyses, picks, skipped) -> dict:
    """리포트와 같은 내용을 기계가 읽기 쉬운 dict로 (Claude 루틴이 코멘트를 쓸 때 사용)."""
    return {
        "generated_at": now.isoformat(),
        "regimes": {
            m: {"label": r.label, "vs_sma50_pct": round(r.vs_sma50 * 100, 2),
                "vs_sma200_pct": round(r.vs_sma200 * 100, 2), "last_date": r.last_date.isoformat()}
            for m, r in regimes.items()
        },
        "picks": [_analysis_dict(a, detail=True) for a in picks],
        "universe": [_analysis_dict(a, detail=False) for a in analyses],
        "skipped": skipped,
    }
