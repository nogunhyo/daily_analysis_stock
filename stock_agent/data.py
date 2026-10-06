"""야후 파이낸스(yfinance) 시세·재무 수집."""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return None
        df = raw[ticker]
    else:
        df = raw
    if not set(OHLCV).issubset(df.columns):
        return None
    df = df[OHLCV].dropna(subset=["Close", "High", "Low"])
    df = df[df["Close"] > 0]
    return df if not df.empty else None


def fetch_prices(tickers: list[str], period: str = "2y", retries: int = 3) -> dict[str, pd.DataFrame]:
    """일봉(수정주가) 다운로드. 실패한 티커만 골라 재시도."""
    result: dict[str, pd.DataFrame] = {}
    pending = list(dict.fromkeys(tickers))
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(
                pending, period=period, auto_adjust=True, group_by="ticker",
                progress=False, threads=True,
            )
        except Exception as exc:  # 네트워크·야후 차단 등
            log.warning("시세 다운로드 실패 (%d/%d): %s", attempt, retries, exc)
            raw = None
        if raw is not None and not raw.empty:
            for t in pending:
                df = _extract(raw, t)
                if df is not None:
                    result[t] = df
        pending = [t for t in pending if t not in result]
        if not pending:
            break
        log.info("재시도 대상 %s", pending)
        time.sleep(5 * attempt)
    if pending:
        log.warning("최종 수집 실패: %s", pending)
    return result


def fetch_earnings_date(ticker: str, today: date) -> date | None:
    """다음 실적발표 예정일 (없거나 실패하면 None)."""
    try:
        cal = yf.Ticker(ticker).calendar or {}
        dates = cal.get("Earnings Date") or []
        upcoming = sorted(d for d in dates if isinstance(d, date) and d >= today)
        return upcoming[0] if upcoming else None
    except Exception as exc:
        log.info("%s 실적일 조회 실패: %s", ticker, exc)
        return None


def fetch_fundamentals(ticker: str) -> dict:
    """참고용 재무지표. 야후 데이터는 누락·오류가 잦으므로 판단의 보조로만 사용."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        log.info("%s 재무 조회 실패: %s", ticker, exc)
        return {}
    return {
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "operating_margin": info.get("operatingMargins"),
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
    }
