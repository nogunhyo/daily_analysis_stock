# Claude 코멘트 루틴 지시문

claude.ai/code/routines 에 등록된 루틴("AI 에이전트 주식 - Claude 코멘트")의 프롬프트 사본입니다.
루틴 프롬프트를 바꿀 때는 이 파일도 같이 고쳐 기록을 남깁니다.

- 실행: 월~토 07:07 KST (GitHub Actions 숫자 리포트 07:00 직후)
- 필요 환경변수 (Claude 클라우드 환경): `STOCK_TELEGRAM_BOT_TOKEN`, `STOCK_TELEGRAM_CHAT_ID`
- 사용량: API 요금이 아니라 Claude 구독 사용량에서 차감

---

<!-- PROMPT START -->
매일 아침 주식 리포트(07:00, GitHub Actions)가 고른 추천 종목에 대해 Claude 코멘트를 작성해 텔레그램으로 보내는 작업이다. 아래 순서대로 수행한다. 저장소 파일을 수정·커밋·푸시하거나 PR을 만들지 않는다.

1. 준비
   - 작업 폴더에 daily_analysis_stock 저장소가 없으면 `git clone https://github.com/nogunhyo/daily_analysis_stock.git` 후 그 폴더로 이동한다.
   - `pip install -q -r requirements.txt`
   - 값은 출력하지 말고 `test -n "$STOCK_TELEGRAM_BOT_TOKEN" && test -n "$STOCK_TELEGRAM_CHAT_ID" && echo ok` 로 환경변수가 있는지만 확인한다. 없으면 작업을 멈추고 이유를 남긴다. 이름이 TELEGRAM_ 으로 시작하는 다른 변수는 다른 루틴의 봇이므로 절대 쓰지 않는다.

2. 데이터
   - `python -m stock_agent --dry-run --no-ai --json /tmp/picks.json` 실행 후 /tmp/picks.json 을 읽는다.
   - picks = 추천 종목(진입·손절·피라미딩 가격, 실적발표일, 경고 포함), universe = 전체 종목 점수표, regimes = 미국·한국 시장 상태.

3. 조사
   - picks 의 각 종목에 대해 웹검색으로 최근 2주 뉴스(실적, 수주, 가이던스, 규제, 경쟁, 공급망)를 확인한다. 한국 종목은 한국어로도 검색한다. 종목당 검색 2~3회 이내.

4. 작성: /tmp/commentary.txt 에 아래 형식으로 쓴다.

   [티커] 종목명
   • 최근 이슈: 날짜 포함 1~2개
   • 병목·해자 점검: 유지/강화/약화 중 하나 + 근거 한 줄
   • 주의할 리스크: 한 줄
   • 셋업과 뉴스: 차트 신호(돌파/눌림 등)를 뉴스가 뒷받침하는지, 엇갈리는지 한 줄
   • Claude 의견: '계획대로' / '신중' / '보류' 중 하나 + 이유 한 줄

   (종목 사이에 빈 줄)

   [오늘의 테마 체크]
   • AI 에이전트 병목(연산·메모리·네트워크·전력 등) 관련 최근 주요 뉴스 1~3개

   [출처]
   • 핵심 출처 URL 최대 6개

   picks 가 비어 있으면 첫 줄에 "오늘은 신규 진입 조건을 만족한 종목이 없습니다."라고 쓰고 [오늘의 테마 체크]와 [출처]만 쓴다.

   작성 규칙
   - 독자는 성장주 가치투자와 손익비 중심 추세매매(피라미딩)를 병행하는 한국인 FPGA 엔지니어다. 한국어로 쉽게 쓰고, 전문용어는 처음 나올 때 괄호로 짧게 풀어 쓴다.
   - 칭찬·과장 없이 객관적 사실과 근거만 쓴다. 긍정 요인과 부정 요인을 함께 본다.
   - 검색으로 확인한 사실에는 날짜를 붙인다. 확인하지 못한 것은 '확인 못함'이라고 쓴다. 추측을 사실처럼 쓰지 않는다.
   - picks.json 의 진입가·손절가·피라미딩 가격은 규칙 기반 계산값이다. 바꾸거나 새 가격을 제시하지 않는다.
   - 실적발표일(earnings_date)이 오늘부터 14일 이내면 반드시 언급한다.
   - 마크다운(**, ##, 표)을 쓰지 말고 일반 텍스트와 '•'만 쓴다. 전체 2,500자 이내.

5. 발송
   - `python -m stock_agent.telegram --send-file /tmp/commentary.txt --title "🤖 Claude 코멘트 ($(TZ=Asia/Seoul date +%m/%d))"`
   - "발송 완료"가 출력되면 끝.

6. 실패 처리
   - 2~5 단계가 실패하면 원인을 한두 줄로 /tmp/commentary.txt 에 쓰고, 제목을 "⚠️ Claude 코멘트 실패"로 바꿔 5단계 명령을 한 번 시도한다.
   - 마지막에 무엇을 했는지 짧게 요약한다.
<!-- PROMPT END -->
