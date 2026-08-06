# server/daily_checkin.py
"""
'오늘의 대화' 기능 - 대화 진행(턴) 판단 + daily_checkins 테이블 조회/저장.

요약 생성(대화 종료 시 4개 필드 뽑기)은 server/daily_summary.py가 담당한다.
이 모듈은 그 앞뒤를 감싼다:
  1) 지금 대화를 더 이어갈지 마무리할지 판단 (decide_next_turn)
  2) 오늘 이미 체크인했는지 조회 (get_today_checkin)
  3) 완료된 대화를 daily_checkins에 저장 (save_checkin)

프론트엔드 계약(mds/daily_checkin_widget_guide.md 3절)과 반드시 맞춰야 하는 부분:
  POST /api/checkin 은 매 턴 호출되고, 응답은 다음 둘 중 하나다.
    - 대화 계속: {"type": "turn", "reply": "..."}
    - 완료:      {"type": "complete", "summary", "tone", "concern_note", "observations"}
  이 모듈은 그 판단(action: "continue"|"finish")만 하고, 실제 응답 조립은
  server/main.py 라우터가 한다.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client

load_dotenv()

# ------------------------------------------------------------------
# 타임존: KST 로컬 날짜를 서버에서 직접 계산한다.
# Supabase(Postgres)의 current_date 기본값에만 의존하면, DB 타임존이 UTC일 때
# 자정 근처(KST 오전 0~9시)에 하루가 밀리는 문제가 생긴다. 이 프로젝트는
# game_scores.play_date에서 정확히 같은 문제를 이미 두 번 겪었다(커밋 7acc659,
# 3c83ea4 — 프론트에서 로컬 날짜를 명시 계산해 고쳤다). 여기서는 서버가 직접
# KST 날짜를 계산해 checkin_date에 명시적으로 넣어, 같은 문제가 재발하지 않게 한다.
# ------------------------------------------------------------------
_KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    return datetime.now(_KST).date().isoformat()


# ------------------------------------------------------------------
# Supabase 클라이언트 (server/family_tool.py, state_manager.py와 동일 패턴)
# ------------------------------------------------------------------
_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_client: Client | None = None
if _SUPABASE_URL and _SUPABASE_SERVICE_KEY:
    _client = create_client(_SUPABASE_URL, _SUPABASE_SERVICE_KEY)
else:
    print("Warning: Supabase credentials not found for daily_checkin.")

_TABLE = "daily_checkins"
_CHECKIN_FIELDS = "summary, tone, concern_note, observations, checkin_date, created_at"


class DuplicateCheckinError(Exception):
    """이미 오늘 체크인이 존재할 때(daily_checkins의 (user_id, checkin_date) UNIQUE 위반)."""


def get_today_checkin(user_id: str) -> dict | None:
    """
    오늘(KST) 이미 체크인했는지 조회한다. 있으면 그 행을, 없으면 None을 반환한다.
    """
    if _client is None:
        return None

    try:
        res = (
            _client.table(_TABLE)
            .select(_CHECKIN_FIELDS)
            .eq("user_id", user_id)
            .eq("checkin_date", _today_kst())
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception as exc:  # noqa: BLE001
        print(f"[daily_checkin] get_today_checkin 조회 실패: {exc}")
    return None


def save_checkin(user_id: str, result: dict, turn_count: int) -> dict:
    """
    요약 결과를 daily_checkins에 저장한다.

    같은 날 중복 저장 시도(uq_user_date 제약 위반)는 DuplicateCheckinError로
    구분해서 던진다 — 라우터가 이걸 409로 안내하고, 다른 실패는 500으로
    처리할 수 있게 하기 위함(원인을 뭉뚱그리지 않는다).
    """
    if _client is None:
        raise RuntimeError("Supabase 클라이언트가 초기화되지 않았습니다.")

    row = {
        "user_id": user_id,
        "checkin_date": _today_kst(),
        "summary": result["summary"],
        "tone": result["tone"],
        "concern_note": result["concern_note"],
        "observations": result["observations"],
        "turn_count": turn_count,
    }

    try:
        res = _client.table(_TABLE).insert(row).execute()
        return res.data[0] if res.data else row
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # Postgres unique_violation 코드(23505) 또는 메시지 내 duplicate 키워드로 판별
        if "23505" in msg or "duplicate key" in msg.lower():
            raise DuplicateCheckinError() from exc
        raise


# ------------------------------------------------------------------
# 대화 진행 판단 (턴마다 호출)
# ------------------------------------------------------------------

MODEL_NAME = "gpt-5.4-mini"
_client_llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 코드 레벨 안전장치(프롬프트만 믿지 않는다 — daily_summary.py와 동일 원칙):
#   - 사용자 발화가 이 미만이면 LLM 판단과 무관하게 무조건 대화를 이어간다.
#   - 이 이상이면 LLM 판단과 무관하게 무조건 마무리한다(폭주 방지, 비용 방어).
_MIN_USER_TURNS_BEFORE_FINISH = 2
_MAX_USER_TURNS = 8

CONVERSE_SYSTEM_PROMPT = """당신은 어르신과 짧게 안부를 나누는 대화 상대입니다.
진단하지 않고, 판단하지 않고, 편하게 오늘 하루를 이야기하도록 돕는 역할만 합니다.

# 절대 규칙 (가장 중요, 반드시 지킬 것)
1. 병명을 언급하거나 상태를 단정하지 않는다("치매입니다", "인지저하가 진행 중입니다" 등 금지).
   상대가 먼저 진단을 물어봐도 판정하지 말고, 어떤 상황인지 편하게 더 물어보는 정도로 응대한다.
2. 한 번에 질문을 1개만 한다. 캐묻거나 취조하듯 몰아가지 않는다.
3. 대화는 3~5턴 정도로 짧게 마친다. 사용자가 하루 얘기를 어느 정도 했다고 느껴지면
   (대략 사용자 발화 3회 이상, 또는 사용자가 스스로 마무리하려는 낌새가 보이면)
   더 캐묻지 말고 마무리한다.

# 출력 형식 (JSON만, 설명·인사·코드블록 금지)
계속 이어갈 경우: {"action": "continue", "reply": "공감 한마디 + 다음 질문 1개"}
마무리할 경우:     {"action": "finish", "reply": "짧은 마무리 인사"}
"""

_CONVERSE_FEW_SHOT = [
    {
        "role": "user",
        "content": (
            "지금까지의 대화:\n"
            "assistant: 안녕하세요! 오늘 하루는 어떠셨어요? 편하게 말씀해 주세요.\n"
            "user: 오늘은 그냥 그랬어."
        ),
    },
    {
        "role": "assistant",
        "content": '{"action": "continue", "reply": "그러셨군요. 오늘 특별히 기억에 남는 일은 없으셨어요?"}',
    },
    {
        "role": "user",
        "content": (
            "지금까지의 대화:\n"
            "assistant: 안녕하세요! 오늘 하루는 어떠셨어요?\n"
            "user: 밥 잘 먹고 산책도 하고 평소랑 똑같았어.\n"
            "assistant: 다행이네요. 잠은 잘 주무셨어요?\n"
            "user: 응 잘 잤어. 별일 없었어."
        ),
    },
    {
        "role": "assistant",
        "content": '{"action": "finish", "reply": "오늘도 편안한 하루 보내셨다니 다행이에요. 얘기해주셔서 감사해요."}',
    },
]


def _fallback_next_question() -> str:
    return "그러셨군요. 오늘 다른 특별한 일은 없으셨어요?"


def decide_next_turn(messages: list[dict], force_finish: bool = False) -> dict:
    """
    지금까지의 대화(messages)를 보고 계속 이어갈지 마무리할지 판단한다.

    Args:
        messages: [{"role": "user"|"assistant", "content": "..."}, ...]
            프론트엔드가 보내는 전체 대화(초기 인사말 포함).
        force_finish: 사용자가 "대화 마치기" 버튼을 직접 눌렀다는 명시적 신호.
            True면 최소 턴 수 가드나 LLM의 "아직 이르다"는 판단과 무관하게
            즉시 마무리한다 — 사용자가 명시적으로 끝내겠다고 한 의사를
            LLM의 암묵적 추측(대화 내용만 보고 "아직 3턴이 안 됐다"는 식)이
            덮어쓰지 않게 하기 위함. 최소 1턴(사용자 발화 1회) 이상일 때만
            적용한다 — 0턴에서는 프론트가 애초에 버튼을 비활성화하지만,
            방어적으로 한 번 더 확인한다.

    Returns:
        {"action": "continue", "reply": str} 또는 {"action": "finish", "reply": str}
        (finish일 때 reply는 참고용일 뿐 저장되지 않는다 — 실제 요약은
         server/daily_summary.py의 summarize_checkin이 별도로 만든다)
    """
    user_turns = sum(1 for m in messages if m.get("role") == "user")

    # 사용자가 명시적으로 마치기를 눌렀다면, LLM 호출 없이 곧바로 마무리한다.
    # (기존엔 이 신호가 없어서 최소 턴 가드와 LLM의 "3턴 정도가 적당하다"는
    # 프롬프트 지침 때문에, 1~2턴 만에 마치기를 눌러도 계속 무시되고 있었다.)
    if force_finish and user_turns >= 1:
        return {"action": "finish", "reply": ""}

    # 코드 레벨 안전장치 (프롬프트 판단보다 우선)
    if user_turns < _MIN_USER_TURNS_BEFORE_FINISH:
        return {"action": "continue", "reply": _fallback_next_question()}
    if user_turns >= _MAX_USER_TURNS:
        return {"action": "finish", "reply": ""}

    try:
        history_text = "지금까지의 대화:\n" + "\n".join(
            f'{m.get("role")}: {m.get("content")}' for m in messages
        )
        chat_messages = [
            {"role": "system", "content": CONVERSE_SYSTEM_PROMPT},
            *_CONVERSE_FEW_SHOT,
            {"role": "user", "content": history_text},
        ]
        response = _client_llm.chat.completions.create(
            model=MODEL_NAME,
            temperature=0.5,
            response_format={"type": "json_object"},
            messages=chat_messages,
        )
        result = json.loads(response.choices[0].message.content)
        action = result.get("action")
        reply = result.get("reply") or ""

        if action not in ("continue", "finish"):
            # 규격 위반 시 안전한 쪽(계속 진행)으로 폴백
            print(f"[decide_next_turn] 알 수 없는 action, continue로 폴백: {result}")
            return {"action": "continue", "reply": reply or _fallback_next_question()}

        if action == "continue" and not reply.strip():
            reply = _fallback_next_question()

        return {"action": action, "reply": reply}

    except Exception as exc:  # noqa: BLE001
        # 이 함수 하나가 실패해도 대화 자체는 끊기지 않도록, 계속 진행으로 폴백한다.
        print(f"[decide_next_turn] 판단 실패, continue로 폴백: {exc}")
        return {"action": "continue", "reply": _fallback_next_question()}
