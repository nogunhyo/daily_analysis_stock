"""Claude + 웹검색으로 추천 종목의 최신 뉴스·해자 점검 코멘트 생성 (선택 기능).

ANTHROPIC_API_KEY 가 없거나 호출이 실패하면 None 을 반환하고, 리포트는 AI 코멘트 없이 발송된다.
가격(진입·손절 등)은 analysis.py 가 계산하며, AI 에게는 수정하지 말라고 지시한다.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from .analysis import StockAnalysis
from .config import AISettings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 한국 개인투자자를 돕는 주식 리서치 애널리스트입니다.
투자 테마: 'AI 에이전트 대중화'로 수요가 폭증하는데 공급이 따라가지 못하는 병목(bottleneck)과,
경쟁자가 쉽게 따라올 수 없는 해자(moat)를 가진 기업.

독자는 성장주 가치투자와 손익비 중심의 중단기 추세매매(피라미딩)를 병행하는 FPGA 엔지니어입니다.

규칙:
- 한국어로, 쉽게 씁니다. 전문용어는 처음 나올 때 괄호로 짧게 풀어 씁니다.
- 칭찬·과장 없이 객관적 사실과 근거만 씁니다. 긍정 요인과 부정 요인을 함께 봅니다.
- 웹검색으로 확인한 사실에는 날짜를 붙입니다. 확인하지 못한 것은 '확인 못함'이라고 씁니다.
- 추측을 사실처럼 쓰지 않습니다.
- 제공된 진입가·손절가·피라미딩 가격은 규칙 기반 계산값이므로 절대 바꾸거나 새 가격을 제시하지 않습니다.
- 마크다운(**, ##, 표)을 쓰지 말고 일반 텍스트와 '•' 글머리표만 씁니다."""

USER_TEMPLATE = """오늘({today}) 규칙 기반 스크리너가 고른 추천 종목입니다.

{payload}

각 종목마다 웹검색으로 최근 2주 이내 뉴스·실적·수주·규제 이슈를 확인하고 아래 형식으로 써 주세요.

[티커] 종목명
• 최근 이슈: (날짜 포함 1~2개)
• 병목·해자 점검: 유지/강화/약화 중 하나 + 근거 한 줄
• 주의할 리스크: 한 줄
• 셋업과 뉴스의 일치 여부: 기술적 셋업(돌파/눌림 등)을 뉴스가 뒷받침하는지, 엇갈리는지 한 줄

마지막에
[오늘의 테마 체크]
• AI 에이전트 병목(연산·메모리·네트워크·전력 등) 관련 최근 주요 뉴스 1~3개

전체 1,800자 이내로 짧게 써 주세요."""


@dataclass
class Commentary:
    text: str
    sources: list[tuple[str, str]] = field(default_factory=list)  # (제목, URL)
    model: str = ""


def _payload(picks: list[StockAnalysis]) -> str:
    items = []
    for a in picks:
        p = a.plan
        items.append(
            {
                "ticker": a.meta.ticker,
                "name": a.meta.name,
                "layer": a.meta.layer,
                "thesis": a.meta.thesis,
                "close": a.close,
                "setup": a.setup.kind,
                "trend_points": f"{a.trend.points}/{a.trend.total}",
                "rs_rank": a.rs_rank,
                "entry": p.entry if p else None,
                "stop": p.stop if p else None,
                "risk_pct": round(p.risk_pct * 100, 1) if p else None,
                "next_earnings": a.earnings_date.isoformat() if a.earnings_date else None,
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=1)


def _final_text(content) -> tuple[str, list[tuple[str, str]]]:
    """마지막 검색 이후의 텍스트 블록만 모아 최종 답변으로 사용."""
    last_tool = -1
    for i, block in enumerate(content):
        if block.type in ("server_tool_use", "web_search_tool_result"):
            last_tool = i
    text_blocks = [b for b in content[last_tool + 1 :] if b.type == "text"]
    if not text_blocks:
        text_blocks = [b for b in content if b.type == "text"]

    sources: dict[str, str] = {}
    for b in text_blocks:
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if url and url not in sources:
                sources[url] = getattr(c, "title", None) or url
    text = "".join(b.text for b in text_blocks).strip()
    return text, [(title, url) for url, title in sources.items()]


def generate_commentary(picks: list[StockAnalysis], today: str, cfg: AISettings) -> Commentary | None:
    if not cfg.enabled or not picks:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY 없음 → AI 코멘트 생략")
        return None

    import anthropic

    client = anthropic.Anthropic(timeout=600.0, max_retries=2)
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": cfg.max_searches}]
    user_msg = {"role": "user", "content": USER_TEMPLATE.format(today=today, payload=_payload(picks))}
    assistant_blocks: list = []

    try:
        for _ in range(4):  # pause_turn(서버 측 검색 루프 일시정지) 이어가기 최대 3회
            messages = [user_msg]
            if assistant_blocks:
                messages.append({"role": "assistant", "content": assistant_blocks})
            with client.beta.messages.stream(
                model=cfg.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.effort},
                tools=tools,
                messages=messages,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # 안전 분류기가 거절하면 서버가 대체 모델로 재시도
            ) as stream:
                response = stream.get_final_message()
            assistant_blocks.extend(response.content)
            if response.stop_reason != "pause_turn":
                break
    except anthropic.AuthenticationError:
        log.error("Anthropic API 키가 유효하지 않음 → AI 코멘트 생략")
        return None
    except anthropic.RateLimitError:
        log.error("Anthropic API 사용량 한도 초과 → AI 코멘트 생략")
        return None
    except anthropic.APIStatusError as exc:
        log.error("Anthropic API 오류 %s: %s → AI 코멘트 생략", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.error("Anthropic API 연결 실패: %s → AI 코멘트 생략", exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("AI 응답 거절됨: %s", response.stop_details)
        return None

    usage = response.usage
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", None)
    log.info(
        "AI 코멘트 완료: model=%s stop=%s in=%s out=%s searches=%s",
        response.model, response.stop_reason, usage.input_tokens, usage.output_tokens, searches,
    )

    text, sources = _final_text(assistant_blocks)
    if not text:
        return None
    if response.stop_reason == "max_tokens":
        text += "\n(출력 길이 한도로 일부 잘림)"
    return Commentary(text=text, sources=sources[:6], model=response.model)
