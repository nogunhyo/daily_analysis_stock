"""config.yaml 로딩."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Settings:
    top_n: int = 3
    max_per_layer: int = 1
    risk_per_trade_pct: float = 1.0
    max_first_tranche_pct: float = 25.0
    pyramid_split: tuple[float, float, float] = (50, 30, 20)
    target_r: float = 3
    atr_period: int = 14
    pivot_lookback: int = 20
    min_base_days: int = 5
    stop_lookback: int = 10
    min_stop_atr: float = 1.0
    max_stop_atr: float = 2.0
    breakout_buy_range_pct: float = 5
    near_pivot_pct: float = 5
    extended_atr: float = 3.0
    extended_sma50_pct: float = 25
    breakout_volume_ratio: float = 1.4
    max_risk_pct_warn: float = 10
    earnings_warn_days: int = 7


@dataclass
class AISettings:
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_searches: int = 8


@dataclass
class StockMeta:
    ticker: str
    name: str
    market: str  # "US" | "KR"
    layer: str
    thesis: str


@dataclass
class Config:
    settings: Settings
    ai: AISettings
    benchmarks: dict[str, str]
    universe: list[StockMeta] = field(default_factory=list)


def _build(cls, raw: dict | None):
    raw = raw or {}
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: 알 수 없는 설정 키 {sorted(unknown)}")
    return cls(**raw)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    settings = _build(Settings, raw.get("settings"))
    settings.pyramid_split = tuple(float(x) for x in settings.pyramid_split)
    if len(settings.pyramid_split) != 3:
        raise ValueError("pyramid_split 은 [1차, 2차, 3차] 3개 값이어야 합니다")

    universe = [_build(StockMeta, item) for item in raw.get("universe", [])]
    for meta in universe:
        if meta.market not in ("US", "KR"):
            raise ValueError(f"{meta.ticker}: market 은 US 또는 KR 이어야 합니다")

    benchmarks = raw.get("benchmarks") or {"US": "QQQ", "KR": "069500.KS"}
    return Config(
        settings=settings,
        ai=_build(AISettings, raw.get("ai")),
        benchmarks=benchmarks,
        universe=universe,
    )
