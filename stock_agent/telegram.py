"""텔레그램 봇 API 발송.

chat id 확인:  python -m stock_agent.telegram --get-chat-id
(먼저 텔레그램에서 내 봇에게 아무 메시지나 보낸 뒤 실행)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
import unicodedata

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# 같은 Claude 클라우드 환경에서 다른 루틴이 TELEGRAM_* 이름으로 다른 봇을 쓰고 있으므로
# 이 프로젝트는 STOCK_ 접두어가 붙은 이름만 읽는다 (다른 봇으로 잘못 보내는 일 방지).
TOKEN_VAR = "STOCK_TELEGRAM_BOT_TOKEN"
CHAT_ID_VAR = "STOCK_TELEGRAM_CHAT_ID"
MAX_LEN = 4000  # 텔레그램 한도 4096자, 여유분 확보


def _env(name: str) -> str | None:
    """환경변수 읽기. 복사·붙여넣기로 섞인 문자 정리 (토큰과 chat id에는 공백이 없음).

    - 전각 문자(：, ０ 등)를 일반 문자로 (NFKC 정규화)
    - 공백·줄바꿈과 눈에 안 보이는 문자(zero-width space 등) 제거
    """
    value = os.environ.get(name)
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value)
    return "".join(c for c in value if not c.isspace() and unicodedata.category(c) != "Cf")


def describe_secret(raw: str) -> str:
    """비밀값 자체는 노출하지 않고 구조만 설명 (문제 진단용)."""
    head, _, tail = raw.partition(":")
    odd = sorted(
        {unicodedata.name(c, f"U+{ord(c):04X}") for c in raw if not (c.isascii() and (c.isalnum() or c in "_-:"))}
    )
    return (
        f"길이 {len(raw)}자, 콜론(:) {raw.count(':')}개, "
        f"첫 콜론 앞 {len(head)}자({'숫자' if head.isdigit() else '숫자 아님'}), "
        f"첫 콜론 뒤 {len(tail)}자(정상: 숫자 8~10자 + 콜론 1개 + 35자), "
        f"허용 안 되는 문자: {', '.join(odd) or '없음'}"
    )


_TOKEN_STD = re.compile(r"\d{5,}:[A-Za-z0-9_-]{35}")  # BotFather 토큰: 봇번호:35자
_TOKEN_ANY = re.compile(r"\d+:[A-Za-z0-9_-]{30,}")  # 길이가 다른 토큰 대비
_CHAT_ID_FULL = re.compile(r"-?\d+|@[A-Za-z][A-Za-z0-9_]{4,}")  # 숫자 id 또는 공개 채널 @이름
_CHAT_ID_PART = re.compile(r"-?\d{5,}(?![\d:])")


def read_token() -> str | None:
    """봇 토큰 읽기. 이름·콜론·따옴표 등이 섞여 저장돼 있으면 토큰 부분만 사용.

    토큰 값은 로그에 절대 남기지 않는다."""
    raw = _env(TOKEN_VAR)
    if not raw:
        return None
    if _TOKEN_STD.fullmatch(raw):
        return raw
    match = _TOKEN_STD.search(raw)
    if match:
        log.warning("%s 에 토큰 외 글자가 섞여 있어 토큰 부분만 사용합니다 (값 정리 권장)", TOKEN_VAR)
        return match.group(0)
    if _TOKEN_ANY.fullmatch(raw):
        return raw
    raise RuntimeError(
        f"{TOKEN_VAR} 형식 오류: BotFather가 준 '숫자:영문' 토큰을 찾을 수 없습니다. "
        f"저장된 값을 확인하세요 [{describe_secret(raw)}]"
    )


def read_chat_id() -> str | None:
    raw = _env(CHAT_ID_VAR)
    if not raw:
        return None
    if _CHAT_ID_FULL.fullmatch(raw):
        return raw
    matches = _CHAT_ID_PART.findall(raw)
    if not matches:
        raise RuntimeError(
            f"{CHAT_ID_VAR} 형식 오류: 숫자로 된 chat id를 찾을 수 없습니다 [{describe_secret(raw)}]"
        )
    log.warning("%s 에 숫자 외 글자가 섞여 있어 숫자 부분만 사용합니다 (값 정리 권장)", CHAT_ID_VAR)
    return matches[-1]


def redact(text: str, token: str) -> str:
    """오류 메시지 등에 섞인 봇 토큰을 가린다 (requests 예외 문구에는 토큰이 든 URL이 포함됨)."""
    return text.replace(token, "<TOKEN>") if token else text


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """줄 단위로 limit 이하 조각으로 분할 (HTML 태그가 줄을 넘지 않도록 작성되어 있음)."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # 한 줄이 너무 긴 예외 상황
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def _post(token: str, method: str, payload: dict, retries: int = 4) -> requests.Response:
    url = API.format(token=token, method=method)
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=30)
        except requests.RequestException as exc:
            log.warning("텔레그램 연결 실패 (%d/%d): %s", attempt + 1, retries, redact(str(exc), token))
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code == 429:
            wait = resp.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(wait + 1)
            continue
        return resp
    raise RuntimeError("텔레그램 발송 재시도 초과")


def send_message(token: str, chat_id: str, text: str) -> None:
    for chunk in split_message(text):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = _post(token, "sendMessage", payload)
        if resp.status_code == 400 and "parse" in resp.text.lower():
            # HTML 파싱 오류 시 서식 없이라도 보낸다
            log.warning("HTML 파싱 오류 → 일반 텍스트로 재발송: %s", resp.text[:200])
            payload.pop("parse_mode")
            payload["text"] = _strip_html(chunk)
            resp = _post(token, "sendMessage", payload)
        if not resp.ok:
            raise RuntimeError(f"텔레그램 발송 실패 {resp.status_code}: {resp.text[:300]}")
        time.sleep(0.5)


def send_all(messages: list[str]) -> None:
    token = read_token()
    chat_id = read_chat_id()
    if not token or not chat_id:
        raise RuntimeError(f"{TOKEN_VAR} / {CHAT_ID_VAR} 환경변수가 필요합니다")
    for msg in messages:
        send_message(token, chat_id, msg)


def _print_chat_ids() -> int:
    token = read_token()
    if not token:
        print(f"{TOKEN_VAR} 환경변수를 먼저 설정하세요.")
        return 1
    try:
        resp = requests.get(API.format(token=token, method="getUpdates"), timeout=30)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"텔레그램 조회 실패: {redact(str(exc), token)}")
        return 1
    if not data.get("ok"):
        print(f"오류: {data}")
        return 1
    seen = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            seen[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not seen:
        print("메시지가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
        return 1
    for chat_id, name in seen.items():
        print(f"chat_id={chat_id}  ({name})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="텔레그램 도구")
    parser.add_argument("--get-chat-id", action="store_true", help="봇에게 온 메시지에서 chat id 출력")
    parser.add_argument("--test", action="store_true", help="테스트 메시지 발송")
    parser.add_argument("--send-file", metavar="PATH", help="텍스트 파일 내용을 발송 (Claude 루틴 코멘트용)")
    parser.add_argument("--title", default="🤖 Claude 코멘트", help="--send-file 메시지 제목")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.get_chat_id:
        return _print_chat_ids()
    if args.test:
        send_all(["✅ 텔레그램 연결 테스트 성공"])
        print("발송 완료")
        return 0
    if args.send_file:
        from .report import build_text_message

        text = Path(args.send_file).read_text(encoding="utf-8").strip()
        if not text:
            print("보낼 내용이 비어 있습니다.")
            return 1
        send_all([build_text_message(args.title, text)])
        print("발송 완료")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
