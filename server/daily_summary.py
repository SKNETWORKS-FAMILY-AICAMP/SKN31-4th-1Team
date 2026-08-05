# daily_summary.py

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()



"""

'오늘의 대화' 기능 - 어르신과 AI가 나눈 하루 대화를 요약 정보로 변환.
이 함수는 진단 도구가 아니며 병명/점수를 매기지 않고,
관찰과 권유 수준의 tone만 붙여서 돌려준다.

"""


###############################################
# 코드 목록 + 검증
###############################################

# tone에 허용되는 값 4개. 이 4개 외의 값은 절대 나오면 안 됨.

TONE_VALUES = [
    "reassure",        # 안심 - 특이사항 없음
    "neutral",          # 보통 - 애매하거나 판단할 것 없음
    "observe",          # 관찰 필요 - 신경 쓰이는 신호 있음
    "suggest_consult",  # 전문가 상담 권함
]


# summarize_checkin이 반드시 돌려줘야 하는 키 목록
SUMMARY_OUTPUT_KEYS = ["summary", "tone", "concern_note", "observations"]



def is_valid_summary(data: dict) -> tuple[bool, str]:
    """
    요약 함수의 출력이 규격을 지켰는지 검사한다.
    (프롬프트만 믿지 않고 코드로 한 번 더 확인하는 안전장치)

    """
    # 정의되지 않은 키가 섞여 있는지 확인
    for key in data:
        if key not in SUMMARY_OUTPUT_KEYS:
            return False, f"정의되지 않은 키: {key}"

    # 필수 키 4개가 다 있는지 확인
    for key in SUMMARY_OUTPUT_KEYS:
        if key not in data:
            return False, f"필수 키 누락: {key}"

    # tone이 정해진 4개 값 중 하나인지 확인
    if data["tone"] not in TONE_VALUES:
        return False, f"허용되지 않은 tone 값: {data['tone']}"

    # observations가 리스트인지 확인
    if not isinstance(data["observations"], list):
        return False, "observations가 리스트가 아님"

    # 우려 톤(observe / suggest_consult)인데 근거(concern_note)가 비어있으면 안 됨
    concern_note = data["concern_note"]
    if not isinstance(concern_note, str):
        return False, "concern_note가 문자열이 아님"
    if data["tone"] in ("observe", "suggest_consult") and not concern_note.strip():
        return False, f"'{data['tone']}' tone인데 concern_note(근거)가 비어 있음"

    return True, ""




##############################################################
# System Prompt + few-shot
##############################################################

TONE_LIST_TEXT = "\n".join(TONE_VALUES)

# 규칙 순서 주의: "단정 금지 / 근거 필수"를 맨 위에 둔 이유는
# extractor.py와 동일 — 아래쪽 규칙은 지켜지지 않는 경향이 있어서 위로 올림.


SUMMARY_SYSTEM_PROMPT = f"""당신은 어르신과 나눈 하루 대화를 요약하는 도구입니다.
대화에 참여하지 않습니다. 오직 JSON만 출력합니다.

# 절대 규칙 (가장 중요, 반드시 지킬 것)
1. 너는 진단하지 않는다. 상태를 단정("치매입니다", "인지저하가 진행 중입니다" 등)
   하지 말고, 관찰과 권유의 말만 써라.
2. tone을 neutral이 아닌 값으로 정하면, concern_note에 그렇게 본 근거를
   반드시 함께 써라. 근거 없이 우려 톤(observe / suggest_consult)을 주지 마라.

# 임무
그날 나눈 대화(messages)를 읽고 아래 4개 항목을 채워 JSON으로 출력하세요.

# 출력 스키마
{{
  "summary": string,          // 그날 대화를 요약한 한 문단
  "tone": string,              // {" | ".join(TONE_VALUES)} 중 하나
  "concern_note": string,      // tone을 그렇게 정한 근거. reassure면 "" 가능
  "observations": [string]     // 대화에서 눈에 띈 키워드 목록. 없으면 []
}}

# tone 값 (이 4개 외에는 쓰지 마세요)
{TONE_LIST_TEXT}

# tone 판단 기준
- reassure: 특이사항 없이 편안한 하루
- neutral: 애매하거나 특별히 판단할 내용 없음
- observe: 신경 쓰이는 신호가 있어 가볍게 지켜보면 좋음
- suggest_consult: 전문가 상담을 권할 정도의 신호

# 그 외 규칙
3. JSON 외에 어떤 설명, 인사, 마크다운 코드블록도 붙이지 마세요.
4. 대화가 거의 없거나 너무 짧으면, summary는 짧게 쓰고 tone은 neutral로 두세요.
"""

# 안심 / 우려 / 애매 세 케이스를 각각 보여주는 few-shot 예시
FEW_SHOT_EXAMPLES = [
    {
        "role": "user",
        "content": (
            "오늘 대화:\n"
            "user: 오늘은 좀 피곤하네. 어제 잠을 설쳤어.\n"
            "assistant: 저런, 요즘 자주 그러세요?\n"
            "user: 한 사나흘 됐나. 자꾸 새벽에 깨.\n"
            "assistant: 낮에는 좀 어떠셨어요?\n"
            "user: 낮엔 괜찮아. 산책도 했고."
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "며칠째 새벽에 깨는 등 수면에 어려움. 낮 활동(산책)은 정상적으로 유지 중.", '
            '"tone": "observe", '
            '"concern_note": "사나흘째 수면 문제가 이어져 가볍게 지켜보길 권함. 낮 생활은 지장 없음.", '
            '"observations": ["새벽에 깸", "수면 부족", "산책 유지"]}'
        ),
    },
    {
        "role": "user",
        "content": (
            "오늘 대화:\n"
            "user: 오늘은 편안했어. 밥도 잘 먹고 산책도 했어.\n"
            "assistant: 다행이네요! 오늘 특별한 일은 없으셨어요?\n"
            "user: 응, 그냥 평소랑 똑같았어."
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "오늘은 편안한 하루. 식사와 산책을 규칙적으로 하셨다고 함.", '
            '"tone": "reassure", "concern_note": "", '
            '"observations": ["규칙적 식사", "산책"]}'
        ),
    },
    {
        "role": "user",
        "content": "오늘 대화:\nuser: 네.\nassistant: 오늘 하루 어떠셨어요?\nuser: 그냥 그랬어.",
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "짧은 대화로 특이사항 확인 어려움.", '
            '"tone": "neutral", "concern_note": "", "observations": []}'
        ),
    },
]




##############################################
# 실제 LLM 호출
##############################################

MODEL_NAME = "gpt-5.4-mini"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))



def _format_messages(messages: list[dict]) -> str:
    """
    [{"role": "user", "content": "..."}] 형태의 대화 목록을
    프롬프트에 넣기 좋은 한 줄짜리 텍스트로 바꾼다.
    """
    lines = [f'{m["role"]}: {m["content"]}' for m in messages]
    return "오늘 대화:\n" + "\n".join(lines)


def summarize_checkin(messages: list[dict]) -> dict:
    """
    그날 나눈 대화를 요약 정보(dict)로 변환한다.

    - temperature=0.3 : 요약은 표현에 약간의 자연스러움이 필요하지만,
      tone 판단은 일관돼야 하므로 완전히 0으로 두지는 않음.
    - response_format=json_object : JSON 앞뒤에 설명이 붙는 실패를 원천 차단.
    - 파싱/검증에 실패하면 예외를 던지지 않고 안전한 기본값(neutral)을 반환한다.
      (이 함수 하나가 실패해도 서비스 전체가 죽으면 안 되므로)

    Args:
        messages: [{"role": "user"|"assistant", "content": "..."}, ...] 형태의
            그날 대화 목록.

    Returns:
        {
            "summary": str,
            "tone": str,             # reassure | neutral | observe | suggest_consult
            "concern_note": str,
            "observations": list,
        }
    """

    if not messages:
        return _empty_result()

    user_content = _format_messages(messages)

    chat_messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        *FEW_SHOT_EXAMPLES,
        {"role": "user", "content": user_content},
    ]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=chat_messages,
        )
        raw = response.choices[0].message.content
        result = json.loads(raw)


     # 검증도 try 블록 안에서 수행 -> 검증 중 타입 에러가 나도 fallback으로 안전하게 처리됨
        is_valid, reason = is_valid_summary(result)
        if not is_valid:
            print(f"[summarize_checkin] 규격 위반, 기본값으로 처리: {reason} / raw={result}")
            return _fallback_result()

        return result





    except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
        print(f"[summarize_checkin] 요약 실패, 기본값으로 처리: {exc}")
        return _fallback_result()


def _empty_result() -> dict:
    """
    대화가 아예 없었을 때(messages == []) 반환할 값.
    '요약 실패'와는 다른 상황이므로 문구를 구분한다.
    """
    return {
        "summary": "오늘은 나눈 대화가 없습니다.",
        "tone": "neutral",
        "concern_note": "",
        "observations": [],
    }


def _fallback_result() -> dict:
    """
    LLM 호출이나 검증에 실패했을 때 돌려줄 안전한 기본값.
    화면이 깨지지 않도록 4개 키를 모두 채워서 반환한다.
    """
    return {
        "summary": "오늘 대화를 요약하지 못했습니다.",
        "tone": "neutral",
        "concern_note": "",
        "observations": [],
    }



#######################################
# 단독 실행 테스트
#######################################

TEST_CASES = [
    # (설명, 대화)
    ("걱정되는 케이스 - 수면문제", [
        {"role": "user", "content": "오늘은 좀 피곤하네. 어제 잠을 설쳤어."},
        {"role": "assistant", "content": "저런, 요즘 자주 그러세요?"},
        {"role": "user", "content": "한 사나흘 됐나. 자꾸 새벽에 깨."},
        {"role": "assistant", "content": "낮에는 좀 어떠셨어요?"},
        {"role": "user", "content": "낮엔 괜찮아. 산책도 했고."},
    ]),
    ("안심 케이스 - 편안한 하루", [
        {"role": "user", "content": "오늘은 편안했어. 밥도 잘 먹고 산책도 했어."},
        {"role": "assistant", "content": "다행이네요! 오늘 특별한 일은 없으셨어요?"},
        {"role": "user", "content": "응, 그냥 평소랑 똑같았어."},
    ]),
    ("애매한 케이스 - 짧은 대화", [
        {"role": "user", "content": "네."},
        {"role": "assistant", "content": "오늘 하루 어떠셨어요?"},
        {"role": "user", "content": "그냥 그랬어."},
    ]),
    ("빈 대화 - 예외 처리 확인", []),
    ("심각한 케이스 - 상담 권유 필요", [
        {"role": "user", "content": "어머니가 며칠 전부터 갑자기 나를 못 알아보셔."},
        {"role": "assistant", "content": "언제부터 그러셨어요?"},
        {"role": "user", "content": "이틀 됐나. 어제는 집도 못 찾아서 길에서 헤매고 계시더라고."},
        {"role": "assistant", "content": "많이 놀라셨겠어요."},
        {"role": "user", "content": "응, 너무 갑작스러워서 어떻게 해야 할지 모르겠어."},
    ]),
    ("과잉반응 체크 - 사소한 깜빡임", [
        {"role": "user", "content": "오늘 우산을 두고 나왔지 뭐야."},
        {"role": "assistant", "content": "저런, 다시 가지러 가셨어요?"},
        {"role": "user", "content": "아니 그냥 하나 새로 샀어. 요즘 날씨도 좋고 기분 괜찮았어."},
    ]),
    ("단정 유도 테스트 - 직접 진단 요청", [
        {"role": "user", "content": "요즘 자꾸 깜빡깜빡하는데 나 이거 치매인 거 아니야?"},
        {"role": "assistant", "content": "어떤 식으로 깜빡하시는지 좀 더 말씀해주시겠어요?"},
        {"role": "user", "content": "약속을 자꾸 잊어버려. 이거 진짜 치매 맞지?"},
    ]),
        ("복합 증상 - 두통 + 물건분실", [
        {"role": "user", "content": "오늘 머리가 좀 아프고 자주 물건을 두고 왔어."},
        {"role": "assistant", "content": "저런, 어디에 두고 오셨어요?"},
        {"role": "user", "content": "지갑도 두고 오고, 우산도 두고 오고 그러네."},
        {"role": "assistant", "content": "요즘 자주 그러세요?"},
        {"role": "user", "content": "요 며칠 유독 그런 것 같아."},
    ]),


]

if __name__ == "__main__":
    for desc, messages in TEST_CASES:
        result = summarize_checkin(messages)
        is_valid, reason = is_valid_summary(result)
        status = "✅" if is_valid else f"⚠️ 검증 실패: {reason}"
        print(f"\n[{desc}]")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(status)
