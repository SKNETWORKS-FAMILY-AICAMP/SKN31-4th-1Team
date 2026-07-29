# modules/extractor.py
"""
modules.extractor

LLM ① 추출기 = "상태 변경 제안" 툴.
사용자 발화 한 줄에서 명시적으로 언급된 정보만 JSON으로 "제안"한다.
저장/병합은 server.state_manager 가 담당하므로 여기서는 DB나 저장을
신경 쓰지 않는다.

구성 (원래는 schema.py / prompt.py / node.py 세 파일이었으나
프로젝트 규모상 하나로 합침):
    1. 코드 목록 + 검증 함수   (schema)
    2. System Prompt + few-shot (prompt)
    3. 실제 LLM 호출 함수       (node)
    4. LangChain @tool 래퍼    (agent 연동용)

server/agent.py 의 TOOLS 리스트에 propose_state_change 를 추가하면,
데이터팀 검색 툴(search_dementia_guideline 등)과 동일한 방식으로
LLM이 스스로 판단해서 호출할 수 있다.

"""
import json
import os

from dotenv import load_dotenv
from langchain_core.tools import tool
from openai import OpenAI

load_dotenv()

_MODEL_NAME = "gpt-5.4-mini"
_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# =========================================================
# 1. 코드 목록 + 검증
# =========================================================

# 증상 코드 (13종, 이 안에서만 사용)
SYMPTOM_CODES = [
    "기억력저하",
    "반복질문",
    "시간장소혼동",
    "언어장애",
    "물건분실",
    "판단력저하",
    "성격변화",
    "흥미상실",
    "일상수행곤란",
    "배회",
    "망상환각",
    "수면장애",
    "배설곤란",
]

# 안전신호 코드 (6종, 이 안에서만 사용)
SAFETY_FLAG_CODES = [
    "급격한변화",
    "실종위험",
    "화기위험",
    "운전위험",
    "자타해위험",
    "낙상반복",
]

PROGRESSION_VALUES = ["점진적", "급격함", "변동적"]

EXTRACTOR_OUTPUT_KEYS = [
    "relation", "age", "symptoms", "duration", "progression",
    "adl_impact", "safety_flags", "region", "note",
]


def is_valid_extraction(data: dict) -> tuple[bool, str]:
    """
    추출기 출력이 규격을 지켰는지 검사한다.
    (프롬프트만 믿지 않고 코드로 한 번 더 검증하는 안전장치)

    Returns:
        (통과 여부, 실패 사유). 통과했으면 사유는 빈 문자열.
    """
    for key in data:
        if key not in EXTRACTOR_OUTPUT_KEYS:
            return False, f"정의되지 않은 키: {key}"

    if "symptoms" in data:
        invalid = [s for s in data["symptoms"] if s not in SYMPTOM_CODES]
        if invalid:
            return False, f"목록 밖 증상 코드: {invalid}"

    if "safety_flags" in data:
        invalid = [s for s in data["safety_flags"] if s not in SAFETY_FLAG_CODES]
        if invalid:
            return False, f"목록 밖 안전신호 코드: {invalid}"

    if "progression" in data and data["progression"] not in PROGRESSION_VALUES:
        return False, f"허용되지 않은 progression 값: {data['progression']}"

    return True, ""


# =========================================================
# 2. System Prompt + few-shot
# =========================================================

_SYMPTOM_LIST_TEXT = "\n".join(SYMPTOM_CODES)
_SAFETY_LIST_TEXT = "\n".join(SAFETY_FLAG_CODES)

# 규칙 1~3(추측 금지 / 키 생략 / null 금지)을 맨 위에 둔 이유:
# "잘 안 될 때" 대응 — 아래쪽 규칙은 잘 안 지켜지는 경향이 있어서 위로 올렸다.
EXTRACTOR_SYSTEM_PROMPT = f"""당신은 치매 상담 대화에서 정보를 추출하는 도구입니다.
대화에 참여하지 않습니다. 사용자에게 말을 걸지 않습니다.
오직 JSON만 출력합니다.

# 절대 규칙 (가장 중요, 반드시 지킬 것)
1. 발화에 없는 내용을 추측하거나 유추하지 마세요.
2. 언급이 없는 항목은 키 자체를 넣지 마세요.
3. null 을 절대 넣지 마세요.

# 임무
사용자(보호자)의 발화에서 명시적으로 언급된 정보만 뽑아 JSON으로 출력하세요.

# 추가 규칙
4. symptoms 와 safety_flags 는 아래 목록의 코드만 사용하세요.
5. 목록에 없는 내용은 note 에 한 문장으로 적으세요.
6. 뽑을 정보가 없으면 빈 객체 {{}} 를 출력하세요.
7. JSON 외에 어떤 설명, 인사, 마크다운 코드블록도 붙이지 마세요.
8. 1인칭 문장("나 ~", "저 ~", "제가 ~")은 기본적으로 화자(보호자) 본인의
   이야기입니다. "~라고 하세요", "~하신대요" 같은 인용 표현이 함께 있을
   때만 환자의 말로 보고 증상을 추출하세요. 인용 표현 없이 1인칭만 있으면
   증상으로 추출하지 말고, note 에 "화자 본인 발화로 보임, 확인 필요"라고만
   적으세요.

# 출력 스키마
{{
  "relation": string,          // 대상자와의 관계. 예: "어머니"
  "age": integer,              // 대상자 나이
  "symptoms": [string],        // 증상 코드 목록
  "duration": string,          // 증상 시작 시점. 예: "6개월 이상"
  "progression": string,       // "점진적" | "급격함" | "변동적"
  "adl_impact": boolean,       // 일상생활(식사/위생/복약)에 지장이 있는가
  "safety_flags": [string],    // 안전신호 코드 목록
  "region": string,            // 거주 지역. 예: "서울 강남구"
  "note": string                // 위에 담기지 않는 맥락 한 문장
}}

# 증상 코드 (이 13개 외에는 쓰지 마세요)
{_SYMPTOM_LIST_TEXT}

# 안전신호 코드 (이 6개 외에는 쓰지 마세요)
{_SAFETY_LIST_TEXT}

# adl_impact 판단 기준
"깜빡한다", "건망증이 있다" 만으로는 true 가 아닙니다.
식사, 위생, 복약, 외출 같은 생활이 실제로 무너질 때만 true 입니다.
언급이 없으면 키를 넣지 마세요. (false 가 아닙니다)
"""

# 마지막 2개(빈 note, 빈 {})가 가장 중요 — "뽑을 게 없으면 안 뽑는 것"을 보여주는 예시.
FEW_SHOT_EXAMPLES = [
    {"role": "user", "content": "어머니가 자꾸 같은 걸 물어보세요"},
    {"role": "assistant", "content": '{"relation": "어머니", "symptoms": ["반복질문"]}'},

    {"role": "user", "content": "78세신데, 한 1년 됐어요. 요즘은 혼자 밥도 못 차려 드세요"},
    {"role": "assistant", "content": (
        '{"age": 78, "duration": "1년쯤", "symptoms": ["일상수행곤란"], "adl_impact": true}'
    )},

    {"role": "user", "content": "가스불을 켜놓고 그냥 나가신 적이 있어요"},
    {"role": "assistant", "content": '{"safety_flags": ["화기위험"]}'},

    {"role": "user", "content": "두 달 전까지는 멀쩡하셨는데 갑자기 확 나빠지셨어요"},
    {"role": "assistant", "content": '{"progression": "급격함", "safety_flags": ["급격한변화"]}'},

    {"role": "user", "content": "그냥 좀 답답해서요..."},
    {"role": "assistant", "content": '{"note": "보호자가 정서적으로 지쳐 있음"}'},

    {"role": "user", "content": "네"},
    {"role": "assistant", "content": "{}"},

    {"role": "user", "content": "나 집가고싶어"},
    {"role": "assistant", "content": '{"note": "화자 본인 발화로 보임, 확인 필요"}'},
      
    {"role": "user", "content": "어머니가 자꾸 '나 집 가고 싶어'라고 하세요"},
    {"role": "assistant", "content": '{"relation": "어머니", "symptoms": ["배회"], ...}'},
]


# =========================================================
# 3. 실제 LLM 호출
# =========================================================

def extractor_node(question: str) -> dict:
    """
    사용자 발화 한 줄에서 명시적으로 언급된 정보만 뽑아 dict로 반환한다.

    - temperature=0 : 추출은 창의성이 필요 없는 작업이라 결과를 최대한 고정한다.
    - response_format=json_object : JSON 앞뒤에 설명이 붙는 실패를 원천 차단한다.
    - 파싱/검증에 실패하면 예외를 던지지 않고 빈 dict를 반환한다.
      (한 턴 못 뽑아도 다음 턴에 다시 시도되므로 서버를 죽이지 않는 것이 중요)

    Args:
        question: 이번 턴의 사용자 발화 원문 한 줄.

    Returns:
        추출된 정보 dict. 뽑을 정보가 없거나 실패하면 빈 dict({}).
    """
    messages = [
        {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
        *FEW_SHOT_EXAMPLES,
        {"role": "user", "content": question},
    ]

    try:
        response = _client.chat.completions.create(
            model=_MODEL_NAME,
            temperature=0,
            response_format={"type": "json_object"},
            messages=messages,
        )
        raw = response.choices[0].message.content
        extracted = json.loads(raw)
    except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
        print(f"[extractor_node] 추출 실패, 빈 dict로 처리: {exc}")
        return {}

    is_valid, reason = is_valid_extraction(extracted)
    if not is_valid:
        print(f"[extractor_node] 규격 위반, 빈 dict로 처리: {reason} / raw={extracted}")
        return {}

    return extracted


# =========================================================
# 4. LangChain @tool 래퍼 — 에이전트(server/agent.py)의 TOOLS 리스트에 그대로 추가
# =========================================================

@tool
def propose_state_change(user_message: str) -> dict:
    """사용자 발화에서 환자·상담 정보를 추출해 '제안'한다 (저장은 하지 않음).

    관계/나이/증상/기간/진행양상/일상생활지장/안전신호/거주지역/기타메모 중
    발화에 실제로 언급된 것만 뽑아 JSON으로 반환한다. 사용자 발화에 새로운
    정보가 담겨 있을 때마다 호출한다. 언급이 없으면 해당 키를 넣지 않고,
    뽑을 게 전혀 없으면 빈 딕셔너리를 반환한다.

    이 결과는 사용자에게 보여줄 답변 문구에 그대로 옮기지 않는다.
    저장/병합은 server.state_manager.merge_state 가 별도로 처리한다.

    Args:
        user_message: 이번 턴의 사용자 발화 원문.

    Returns:
        추출된 상태 patch dict. 뽑을 정보가 없으면 빈 dict({}).
    """
    return extractor_node(user_message)

# =========================================================
# 단독 실행 테스트
# =========================================================
_TEST_CASES = [
    ("어머니가 자꾸 같은 걸 물어보세요", {"relation": "어머니", "symptoms": ["반복질문"]}),
    ("78세신데, 한 1년 됐어요. 요즘은 혼자 밥도 못 차려 드세요",
     {"age": 78, "duration": "1년쯤", "symptoms": ["일상수행곤란"], "adl_impact": True}),
    ("가스불을 켜놓고 그냥 나가신 적이 있어요", {"safety_flags": ["화기위험"]}),
    ("두 달 전까지는 멀쩡하셨는데 갑자기 확 나빠지셨어요",
     {"progression": "급격함", "safety_flags": ["급격한변화"]}),
    ("그냥 좀 답답해서요...", {"note": "보호자가 정서적으로 지쳐 있음"}),
    ("네", {}),
]

if __name__ == "__main__":
    # 실행 방법 (프로젝트 루트에서): python -m modules.extractor
    for utterance, expected in _TEST_CASES:
        result = extractor_node(utterance)
        status = "✅" if result.keys() == expected.keys() else "⚠️ (키 구성 다름, 확인 필요)"
        print(f"\n입력: {utterance}")
        print(f"결과: {result}  {status}")