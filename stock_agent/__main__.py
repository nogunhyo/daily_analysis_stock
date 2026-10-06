"""매일 아침 리포트 실행 진입점.

    python -m stock_agent            # 분석 + 텔레그램 발송
    python -m stock_agent --dry-run  # 발송 없이 화면 출력
    python -m stock_agent --no-ai    # AI 코멘트 생략
"""
from __future__ import annotations

import argparse
import html
import logging
import re
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from . import telegram
from .ai_commentary import generate_commentary
from .analysis import add_indicators, analyze_universe, apply_earnings, market_regime, select_picks
from .config import DEFAULT_CONFIG_PATH, load_config
from .data import fetch_earnings_date, fetch_fundamentals, fetch_prices
from .report import build_messages

log = logging.getLogger("stock_agent")
KST = ZoneInfo("Asia/Seoul")


def run(config_path: str, dry_run: bool, use_ai: bool) -> list[str]:
    cfg = load_config(config_path)
    s = cfg.settings
    now = datetime.now(KST)
    today = now.date()

    tickers = [m.ticker for m in cfg.universe] + list(cfg.benchmarks.values())
    log.info("시세 수집: %d개 티커", len(tickers))
    prices = fetch_prices(tickers)

    missing_bench = [b for b in cfg.benchmarks.values() if b not in prices]
    if missing_bench:
        raise RuntimeError(f"벤치마크 시세 수집 실패: {missing_bench}")
    benchmarks = {mkt: add_indicators(prices[t], s.atr_period) for mkt, t in cfg.benchmarks.items()}
    regimes = {mkt: market_regime(df) for mkt, df in benchmarks.items()}

    analyses, skipped = analyze_universe(prices, cfg.universe, benchmarks, regimes, s)
    if not analyses:
        raise RuntimeError("분석 가능한 종목이 없습니다 (시세 수집 실패)")

    # 후보에 대해서만 실적일 조회 → 임박하면 감점 후 재정렬
    candidates = [a for a in analyses if a.actionable][: s.top_n * 4]
    for a in candidates:
        apply_earnings(a, fetch_earnings_date(a.meta.ticker, today), today, s)
    picks = select_picks(candidates, s.top_n, s.max_per_layer)
    for a in picks:
        a.fundamentals = fetch_fundamentals(a.meta.ticker)
    analyses.sort(key=lambda a: a.score, reverse=True)

    commentary = generate_commentary(picks, today.isoformat(), cfg.ai) if use_ai else None

    messages = build_messages(now, regimes, cfg.benchmarks, analyses, picks, skipped, commentary, s)
    if dry_run:
        for msg in messages:
            print(html.unescape(re.sub(r"<[^>]+>", "", msg)))
            print("-" * 60)
    else:
        telegram.send_all(messages)
        log.info("텔레그램 발송 완료 (%d개 메시지)", len(messages))
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 에이전트 병목·해자 종목 데일리 리포트")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 발송 없이 출력만")
    parser.add_argument("--no-ai", action="store_true", help="Claude 코멘트 생략")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run(args.config, args.dry_run, not args.no_ai)
    except Exception as exc:
        log.error("리포트 생성 실패:\n%s", traceback.format_exc())
        if not args.dry_run:
            try:
                telegram.send_all([f"❌ 오늘 리포트 생성 실패\n{type(exc).__name__}: {str(exc)[:500]}"])
            except Exception:
                log.error("실패 알림 발송도 실패")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
