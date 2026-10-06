"""기술적 지표, 추세·셋업 판정, 진입/손절/피라미딩 가격 계산.

숫자는 전부 여기서 규칙대로 계산한다. (AI는 숫자를 만들지 않고 해설만 붙인다.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import Settings, StockMeta

# 셋업 종류
BREAKOUT = "돌파 직후"
PIVOT = "돌파 대기"
PULLBACK = "눌림목"
EXTENDED = "과열(추격 금지)"
NO_SETUP = "셋업 없음"
ACTIONABLE = (BREAKOUT, PIVOT, PULLBACK)

MIN_ROWS = 260  # 52주 고저·200일선 계산에 필요한 최소 거래일 수


# ─────────────────────────── 지표 ───────────────────────────

def add_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"]
    out["ema21"] = close.ewm(span=21, adjust=False).mean()
    out["sma50"] = close.rolling(50).mean()
    out["sma150"] = close.rolling(150).mean()
    out["sma200"] = close.rolling(200).mean()
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - prev_close).abs(),
            (out["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.ewm(alpha=1 / atr_period, adjust=False).mean()  # Wilder ATR
    out["vol50"] = out["Volume"].rolling(50).mean()
    return out


def period_return(close: pd.Series, days: int) -> float:
    if len(close) <= days:
        return math.nan
    return float(close.iloc[-1] / close.iloc[-days - 1] - 1)


def momentum_score(close: pd.Series) -> float:
    """IBD 상대강도 근사: 최근 3개월 40%, 6·9·12개월 각 20%."""
    weights = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
    return sum(w * period_return(close, d) for d, w in weights.items())


# ─────────────────────────── 시장 상태 ───────────────────────────

@dataclass
class Regime:
    label: str  # 강세 / 중립 / 약세
    risk_mult: float
    vs_sma50: float
    vs_sma200: float
    last_date: date


def market_regime(df: pd.DataFrame) -> Regime:
    last = df.iloc[-1]
    close = last["Close"]
    vs50 = close / last["sma50"] - 1
    vs200 = close / last["sma200"] - 1
    if close > last["sma50"] and close > last["sma200"] and last["sma50"] > last["sma200"]:
        label, mult = "강세", 1.0
    elif close < last["sma200"]:
        label, mult = "약세", 0.5
    else:
        label, mult = "중립", 0.75
    return Regime(label, mult, float(vs50), float(vs200), df.index[-1].date())


# ─────────────────────────── 추세 템플릿 ───────────────────────────

@dataclass
class TrendCheck:
    points: int
    total: int
    failed: list[str]

    @property
    def stage(self) -> str:
        if self.points >= 7:
            return "상승추세"
        if self.points >= 5:
            return "추세 형성 중"
        return "약세"


def trend_template(df: pd.DataFrame, rs_rank: float) -> TrendCheck:
    """마크 미너비니의 트렌드 템플릿 8개 조건 (RS는 유니버스 내 순위로 대체)."""
    last = df.iloc[-1]
    close = last["Close"]
    hi52 = df["High"].iloc[-252:].max()
    lo52 = df["Low"].iloc[-252:].min()
    sma200_month_ago = df["sma200"].iloc[-22]
    conditions = [
        ("종가>150·200일선", close > last["sma150"] and close > last["sma200"]),
        ("150일선>200일선", last["sma150"] > last["sma200"]),
        ("200일선 1개월 상승", last["sma200"] > sma200_month_ago),
        ("50일선>150·200일선", last["sma50"] > last["sma150"] and last["sma50"] > last["sma200"]),
        ("종가>50일선", close > last["sma50"]),
        ("52주 저점+30%↑", close >= lo52 * 1.3),
        ("52주 고점 -25% 이내", close >= hi52 * 0.75),
        ("RS≥70", rs_rank >= 70),
    ]
    failed = [name for name, ok in conditions if not ok]
    return TrendCheck(points=len(conditions) - len(failed), total=len(conditions), failed=failed)


# ─────────────────────────── 셋업 판정 ───────────────────────────

@dataclass
class Setup:
    kind: str
    entry: float | None  # 반올림 전 진입 기준가
    entry_rule: str  # 사람이 읽는 진입 방법 설명
    pivot: float
    dist_to_pivot: float  # (피벗-종가)/종가
    vol_ratio: float
    volume_confirmed: bool = False


def detect_setup(df: pd.DataFrame, s: Settings) -> Setup:
    last = df.iloc[-1]
    close, atr, ema21, sma50 = last["Close"], last["atr"], last["ema21"], last["sma50"]
    lookback = s.pivot_lookback
    window = df["High"].iloc[-lookback - 1 : -1]  # 오늘 제외 직전 N일
    prev_pivot = float(window.max())
    base_days = len(window) - 1 - int(window.values.argmax())  # 직전 고점 이후 횡보한 거래일 수
    pivot = max(prev_pivot, float(last["High"]))
    vol_ratio = float(last["Volume"] / last["vol50"]) if last["vol50"] > 0 else math.nan
    dist = (pivot - close) / close
    extended = (close - ema21) / atr > s.extended_atr or close / sma50 - 1 > s.extended_sma50_pct / 100

    if close > prev_pivot:
        if base_days >= s.min_base_days:  # 베이스(횡보 구간)를 뚫은 진짜 돌파
            if close <= prev_pivot * (1 + s.breakout_buy_range_pct / 100):
                return Setup(
                    BREAKOUT, float(close),
                    f"{base_days}일 횡보 후 피벗 돌파 → 피벗+{s.breakout_buy_range_pct:g}% 이내에서 매수, 그 위는 추격 금지",
                    prev_pivot, dist, vol_ratio, vol_ratio >= s.breakout_volume_ratio,
                )
            return Setup(
                EXTENDED, max(prev_pivot, float(ema21)),
                "돌파 후 이격 과다 → 이전 피벗/21일 EMA까지 되돌림 시 지정가 매수",
                prev_pivot, dist, vol_ratio,
            )
        if not extended:
            return Setup(
                NO_SETUP, None, "연속 신고가 진행 중(베이스 없음) → 새 베이스 또는 21일 EMA 눌림 대기",
                pivot, dist, vol_ratio,
            )

    if extended:
        return Setup(
            EXTENDED, float(ema21),
            "단기 과열 → 21일 EMA까지 눌림 시 지정가 매수",
            pivot, dist, vol_ratio,
        )

    if dist <= s.near_pivot_pct / 100:
        return Setup(
            PIVOT, pivot + 0.1 * atr,
            f"피벗(최근 {s.pivot_lookback}일 고가) 돌파 시 매수 (증권사 '감시주문/스톱 매수' 활용)",
            pivot, dist, vol_ratio,
        )

    drawdown = (pivot - close) / pivot
    if close >= sma50 and close <= ema21 + 0.5 * atr and 0.03 <= drawdown <= 0.25:
        return Setup(
            PULLBACK, float(last["High"]) + 0.1 * atr,
            "21일 EMA 부근 눌림 → 최근 거래일 고가를 넘으면(반등 확인) 매수",
            pivot, dist, vol_ratio,
        )

    return Setup(NO_SETUP, None, "명확한 진입 자리 없음 → 관망", pivot, dist, vol_ratio)


# ─────────────────────────── 호가 단위 ───────────────────────────

def krx_tick(price: float) -> float:
    """KRX 호가가격단위 (2023년 개편, 코스피·코스닥 공통)."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def tick_size(price: float, market: str) -> float:
    return krx_tick(price) if market == "KR" else 0.01


def round_price(price: float, market: str, mode: str = "nearest") -> float:
    tick = tick_size(price, market)
    units = price / tick
    if mode == "up":
        units = math.ceil(units - 1e-9)
    elif mode == "down":
        units = math.floor(units + 1e-9)
    else:
        units = round(units)
    return round(units * tick, 2)


# ─────────────────────────── 매매 계획 ───────────────────────────

@dataclass
class TradePlan:
    entry: float
    stop: float
    r: float  # 1R = 진입가 - 손절가
    risk_pct: float  # 손절폭 (진입가 대비)
    add1: float  # 2차 추가매수가 (+1R)
    add1_stop: float  # 2차 추가 후 손절가 (= 진입가, 본전)
    add2: float  # 3차 추가매수가 (+2R)
    add2_stop: float  # 3차 추가 후 손절가 (= +1R)
    target: float  # 1차 목표 (+3R)
    target_r: float
    tranche_pcts: tuple[float, float, float]  # 계좌 대비 1·2·3차 비중 %
    risk_budget_pct: float  # 이번 매매의 계좌 위험 %


def build_plan(df: pd.DataFrame, entry_raw: float, market: str, s: Settings, risk_mult: float = 1.0) -> TradePlan:
    atr = float(df["atr"].iloc[-1])
    entry = round_price(entry_raw, market, "nearest")

    # 손절: 최근 N일 저점 아래(구조) 와 2 ATR(변동성) 중 진입가에 가까운 쪽,
    #       단 1 ATR보다 가까우면 1 ATR로 (일상적 흔들림에 털리지 않게)
    structure_stop = float(df["Low"].iloc[-s.stop_lookback :].min()) - 0.25 * atr
    vol_stop = entry - s.max_stop_atr * atr
    stop_raw = min(max(structure_stop, vol_stop), entry - s.min_stop_atr * atr)
    stop = round_price(stop_raw, market, "down")

    r = entry - stop
    add1 = round_price(entry + r, market, "up")
    add2 = round_price(entry + 2 * r, market, "up")
    target = round_price(entry + s.target_r * r, market, "nearest")
    risk_pct = r / entry

    risk_budget = s.risk_per_trade_pct * risk_mult
    first = min(risk_budget / (risk_pct * 100) * 100, s.max_first_tranche_pct)
    split1, split2, split3 = s.pyramid_split
    tranches = (first, first * split2 / split1, first * split3 / split1)

    return TradePlan(
        entry=entry, stop=stop, r=r, risk_pct=risk_pct,
        add1=add1, add1_stop=entry,
        add2=add2, add2_stop=add1,
        target=target, target_r=s.target_r,
        tranche_pcts=tranches, risk_budget_pct=risk_budget,
    )


# ─────────────────────────── 종목 종합 ───────────────────────────

SETUP_POINTS = {BREAKOUT: 20, PIVOT: 18, PULLBACK: 16, EXTENDED: 5, NO_SETUP: 0}


@dataclass
class StockAnalysis:
    meta: StockMeta
    last_date: date
    close: float
    atr: float
    rs_rank: float
    excess_63d: float
    trend: TrendCheck
    setup: Setup
    plan: TradePlan | None
    score: float
    warnings: list[str] = field(default_factory=list)
    earnings_date: date | None = None
    fundamentals: dict = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.trend.stage == "상승추세" and self.setup.kind in ACTIONABLE and self.plan is not None


def score_stock(trend: TrendCheck, rs_rank: float, setup: Setup, plan: TradePlan | None) -> float:
    score = trend.points / trend.total * 40 + rs_rank * 0.3
    points = SETUP_POINTS[setup.kind]
    if setup.kind == BREAKOUT and not setup.volume_confirmed:
        points = 12
    score += points
    if plan is not None:
        risk = plan.risk_pct * 100
        score += 10 if risk <= 5 else 7 if risk <= 7 else 4 if risk <= 10 else 0
    return round(score, 1)


def analyze_universe(
    prices: dict[str, pd.DataFrame],
    universe: list[StockMeta],
    benchmarks: dict[str, pd.DataFrame],
    regimes: dict[str, Regime],
    s: Settings,
) -> tuple[list[StockAnalysis], list[str]]:
    """유니버스 전체를 분석해 점수순으로 반환. 두 번째 값은 건너뛴 종목 사유."""
    skipped: list[str] = []
    prepared: dict[str, pd.DataFrame] = {}
    for meta in universe:
        df = prices.get(meta.ticker)
        if df is None or len(df) < MIN_ROWS:
            skipped.append(f"{meta.ticker}(데이터 부족)")
            continue
        bench_last = benchmarks[meta.market].index[-1]
        if (bench_last - df.index[-1]).days > 7:
            skipped.append(f"{meta.ticker}(시세 갱신 안 됨: {df.index[-1].date()})")
            continue
        prepared[meta.ticker] = add_indicators(df, s.atr_period)

    momentum = pd.Series({t: momentum_score(df["Close"]) for t, df in prepared.items()}).dropna()
    rs_ranks = (momentum.rank(pct=True) * 100).round(0)

    results: list[StockAnalysis] = []
    for meta in universe:
        df = prepared.get(meta.ticker)
        if df is None or meta.ticker not in rs_ranks:
            continue
        rs = float(rs_ranks[meta.ticker])
        bench_close = benchmarks[meta.market]["Close"]
        excess = period_return(df["Close"], 63) - period_return(bench_close, 63)
        trend = trend_template(df, rs)
        setup = detect_setup(df, s)
        regime = regimes[meta.market]
        plan = build_plan(df, setup.entry, meta.market, s, regime.risk_mult) if setup.entry is not None else None

        warnings: list[str] = []
        if plan and plan.risk_pct * 100 > s.max_risk_pct_warn:
            warnings.append(f"손절폭 {plan.risk_pct*100:.1f}%로 큼 → 비중 자동 축소됨")
        if setup.kind == BREAKOUT and not setup.volume_confirmed:
            warnings.append(f"돌파 거래량 부족 (50일 평균의 {setup.vol_ratio:.1f}배)")
        if regime.label == "약세":
            warnings.append("시장 약세 구간 → 1회 위험 절반으로 축소")

        last = df.iloc[-1]
        results.append(
            StockAnalysis(
                meta=meta,
                last_date=df.index[-1].date(),
                close=float(last["Close"]),
                atr=float(last["atr"]),
                rs_rank=rs,
                excess_63d=excess,
                trend=trend,
                setup=setup,
                plan=plan,
                score=score_stock(trend, rs, setup, plan),
                warnings=warnings,
            )
        )
    results.sort(key=lambda a: a.score, reverse=True)
    return results, skipped


def apply_earnings(a: StockAnalysis, earnings_date: date | None, today: date, s: Settings) -> None:
    """실적발표가 임박하면 감점·경고 (발표 후 갭으로 손절선이 무의미해질 수 있음)."""
    a.earnings_date = earnings_date
    if earnings_date is None:
        return
    days = (earnings_date - today).days
    if 0 <= days <= s.earnings_warn_days:
        a.score = round(a.score - 10, 1)
        a.warnings.append(f"실적발표 D-{days} ({earnings_date:%m/%d}) → 갭 위험, 발표 후 진입 고려")


def layer_group(meta: StockMeta) -> str:
    return meta.layer.split("·")[0]


def select_picks(candidates: list[StockAnalysis], top_n: int, max_per_layer: int) -> list[StockAnalysis]:
    """점수순으로 고르되 같은 병목 구간은 max_per_layer 개까지만 (같이 움직이는 종목 쏠림 방지)."""
    picks: list[StockAnalysis] = []
    counts: dict[str, int] = {}
    for a in sorted(candidates, key=lambda x: x.score, reverse=True):
        group = layer_group(a.meta)
        if counts.get(group, 0) >= max_per_layer:
            continue
        picks.append(a)
        counts[group] = counts.get(group, 0) + 1
        if len(picks) == top_n:
            break
    return picks
