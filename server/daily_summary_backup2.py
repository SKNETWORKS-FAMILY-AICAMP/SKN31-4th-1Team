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
# 체크인 슬롯 (agent.py가 대화 중 자연스럽게 물어보고 뽑아낸 구조화 필드)
###############################################

# agent.py에서 대화 중 유도 질문으로 얻어낸 값들을 이 형태로 넘겨받는다.
# 전부 선택 필드 -> 그날 물어본 것만 채워서 넘기면 됨.
#
# CheckinFields 예시:
# {
#     "sleep_quality": "불량",       # 양호 | 불량 | None
#     "physical_activity": "함",     # 함 | 안함 | None
#     "social_contact": "있음",      # 있음 | 없음 | None
#     "mood": "저조",                # 좋음 | 보통 | 저조 | None
#     "meal_status": "잘 챙김",      # 잘 챙김 | 부실함 | None
#     "physical_note": "다리 통증",  # 통증/피로 등 자유 텍스트, 원인 설명용
# }

CHECKIN_FIELD_KEYS = [
    "sleep_quality",
    "physical_activity",
    "social_contact",
    "mood",
    "meal_status",
    "physical_note",
]


def _format_checkin_fields(checkin_fields: dict | None) -> str:
    """
    구조화된 체크인 필드를 프롬프트용 텍스트로 변환한다.
    값이 없거나 전부 None이면 빈 문자열 반환 (프롬프트에 섹션 자체를 안 넣기 위함).
    """
    if not checkin_fields:
        return ""

    lines = []
    for key in CHECKIN_FIELD_KEYS:
        value = checkin_fields.get(key)
        if value:
            lines.append(f"- {key}: {value}")

    if not lines:
        return ""

    return "체크인 항목 (사용자가 직접 답한 구조화 정보):\n" + "\n".join(lines)


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

    # 반대 방향도 검사: reassure/neutral인데 concern_note에 우려 문구가 있으면 안 됨.
    # (배지는 "특별한 이슈 없음"으로 뜨는데 본문엔 주의사항이 뜨는 모순 방지)
    if data["tone"] in ("reassure", "neutral") and concern_note.strip():
        return False, (
            f"'{data['tone']}' tone인데 concern_note가 채워져 있음 "
            f"(우려 내용이 있다면 tone을 observe 이상으로 올려야 함): {concern_note}"
        )

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
3. 산만함, 집중력 저하, 일 마무리 못함 같은 증상이 통증/피로/수면부족/
   날씨 등 명백한 신체적·상황적 원인으로 충분히 설명되면, 그 자체만으로
   tone을 observe/suggest_consult로 올리지 마라. 그런 원인 없이 반복되거나
   원인과 무관하게 인지 관련 신호(기억, 지남력, 판단력 등)가 보일 때만
   tone을 올려라.
4. '체크인 항목'이 함께 주어지면, 대화 텍스트만으로 원인을 추측하지 말고
   체크인 항목에 명시된 값을 우선 근거로 삼아라. 예를 들어 physical_note에
   원인이 적혀 있으면 그걸 concern_note에 반영해라.
5. 가스불/문 잠금/복용약처럼 안전과 직결된 사건은, 우산을 두고 나온 것 같은
   사소한 분실과 다르다. 단 한 번만 있었더라도 그 자체로 tone을
   최소 observe로 올리고 concern_note에 안전 관련 근거를 써라.
   이때 concern_note 문장은 실제 위험(화재, 실종, 복용 오류 등)을 명확히
   짚어야 한다 — "가볍게", "별일 아니지만", "크게 걱정할 정도는 아니지만"처럼
   위험을 축소하는 표현은 쓰지 마라. tone 등급이 observe라고 해서 문장까지
   가볍게 쓸 필요는 없다. (아래 tone 판단 기준의 "가볍게 지켜보면 좋음"은
   observe의 일반적 정의이지, 안전 직결 사건에는 적용하지 않는다.)
   tone을 reassure나 neutral로 줄 때는 concern_note를 반드시 빈 문자열("")로
   두어라 — concern_note에 쓸 우려 내용이 있다면 tone도 observe 이상이어야 한다.

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
6. JSON 외에 어떤 설명, 인사, 마크다운 코드블록도 붙이지 마세요.
7. 대화가 거의 없거나 너무 짧으면, summary는 짧게 쓰고 tone은 neutral로 두세요.
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
        "content": (
            "오늘 대화:\n"
            "user: 오늘 다리가 좀 아파서 힘들었어.\n"
            "assistant: 많이 불편하셨겠어요.\n"
            "user: 응, 아프니까 정신도 산만하고 하던 일도 끝까지 못하고 자꾸 다른 일로 넘어가게 되더라고.\n"
            "assistant: 다리는 언제부터 아프셨어요?\n"
            "user: 어제부터. 며칠 쉬면 괜찮아질 것 같아."
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "다리 통증으로 힘든 하루. 통증 때문에 집중이 흐트러지고 '
            '하던 일을 끝까지 마무리하지 못함.", '
            '"tone": "neutral", "concern_note": "", '
            '"observations": ["다리 통증", "일시적 산만함"]}'
        ),
    },
    {
        "role": "user",
        "content": (
            "체크인 항목 (사용자가 직접 답한 구조화 정보):\n"
            "- physical_note: 다리 통증\n\n"
            "오늘 대화:\n"
            "user: 오늘 다리가 좀 아파서 힘들었어.\n"
            "assistant: 많이 불편하셨겠어요.\n"
            "user: 응, 아프니까 정신도 산만하고 하던 일도 끝까지 못하고 자꾸 다른 일로 넘어가게 되더라고.\n"
            "assistant: 다리는 언제부터 아프셨어요?\n"
            "user: 어제부터. 며칠 쉬면 괜찮아질 것 같아."
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "다리 통증으로 힘든 하루. 통증 때문에 집중이 흐트러지고 '
            '하던 일을 끝까지 마무리하지 못함.", '
            '"tone": "neutral", "concern_note": "", '
            '"observations": ["다리 통증", "일시적 산만함"]}'
        ),
    },
    {
        "role": "user",
        "content": (
            "오늘 대화:\n"
            "user: 아까 나오는데 가스불을 안 끄고 나온 걸 알았어. 다행히 별일은 없었어.\n"
            "assistant: 많이 놀라셨겠어요. 그 외에는 별일 없으셨어요?\n"
            "user: 응, 그거 말고는 특별한 일 없었어."
        ),
    },
    {
        "role": "assistant",
        "content": (
            '{"summary": "가스불을 끄지 않고 나온 일이 있었고, 그 외에는 특별한 일은 없었다고 하셨습니다.", '
            '"tone": "observe", '
            '"concern_note": "가스불을 켠 채 외출해 화재 위험이 있었던 사안입니다. 안전을 위해 확인이 필요합니다. 다른 특별한 일은 없다고 하셨습니다.", '
            '"observations": ["가스불 끄지 않고 외출"]}'
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



def _format_messages(messages: list[dict], checkin_fields: dict | None = None) -> str:
    """
    대화 목록(+선택적 체크인 필드)을 프롬프트용 텍스트로 바꾼다.
    체크인 필드가 있으면 대화 앞에 붙여서, LLM이 원인을 추측하는 대신
    명시된 값을 먼저 근거로 쓰게 한다.
    """
    field_text = _format_checkin_fields(checkin_fields)
    lines = [f'{m["role"]}: {m["content"]}' for m in messages]
    convo_text = "오늘 대화:\n" + "\n".join(lines)

    if field_text:
        return f"{field_text}\n\n{convo_text}"
    return convo_text


def summarize_checkin(messages: list[dict], checkin_fields: dict | None = None) -> dict:
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
        checkin_fields: agent.py가 체크인 질문(수면/신체활동/사회적교류/기분/식사)에서
            뽑아낸 구조화 값. 없으면 대화 텍스트만으로 판단.

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

    user_content = _format_messages(messages, checkin_fields)

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
    # (설명, 대화, checkin_fields)
    ("체크인 필드 있음 - 통증으로 인한 산만함 (neutral 유지 확인)", [
        {"role": "user", "content": "오늘 다리가 좀 아파서 힘들었어."},
        {"role": "assistant", "content": "많이 불편하셨겠어요."},
        {"role": "user", "content": "응, 아프니까 산만하고 하던 일 마무리를 못하고 자꾸 다른 일로 넘어가."},
    ], {"physical_note": "다리 통증", "sleep_quality": "양호"}),
    ("안전 직결 - 가스불 안 끄고 외출 (observe로 격상돼야 함)", [
        {"role": "user", "content": "아까 나오는데 가스불을 안 끄고 나온 걸 알았어. 다행히 별일은 없었어."},
        {"role": "assistant", "content": "많이 놀라셨겠어요. 그 외에는 별일 없으셨어요?"},
        {"role": "user", "content": "응, 그거 말고는 특별한 일 없었어."},
    ], None),
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
    for case in TEST_CASES:
        # 케이스는 (설명, 대화) 또는 (설명, 대화, checkin_fields) 둘 다 허용
        desc, messages, *rest = case
        checkin_fields = rest[0] if rest else None

        result = summarize_checkin(messages, checkin_fields)
        is_valid, reason = is_valid_summary(result)
        status = "✅" if is_valid else f"⚠️ 검증 실패: {reason}"
        print(f"\n[{desc}]")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(status)