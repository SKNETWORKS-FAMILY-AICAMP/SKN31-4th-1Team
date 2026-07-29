# modules/agent.py
"""치매 가이드 챗봇 에이전트. get_answer(question)->str / get_structured_answer(question)->dict"""
from typing import List, Literal, Optional, Union

# 외부 라이브러리
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# 데이터팀/추출팀이 만든 tool
from vector_db.vector_search_tool import search_dementia_guideline
from modules.extractor import propose_state_change
from graph_db.graph_search_tool import (
    get_centers_by_sido,
    get_centers_by_sigungu,
    get_centers_by_program,
    get_programs_by_center,
    search_center_by_name,
    get_operator_by_center,
    get_centers_by_operator,
    get_sido_list,
    get_sigungu_list,
    flexible_graph_search,
)

load_dotenv()  # .env 로드

_MODEL_NAME = "gpt-5.4-mini"  # 사용할 LLM 모델명

# 에이전트가 사용할 tool 목록
TOOLS = [
    search_dementia_guideline,       # 증상/가이드라인 검색
    propose_state_change,            # 환자 상태 정보 추출

    get_centers_by_sido,             # 시/도별 센터 조회
    get_centers_by_sigungu,          # 시군구별 센터 조회
    get_centers_by_program,          # 프로그램으로 센터 역조회
    get_programs_by_center,          # 센터가 제공하는 프로그램 조회
    search_center_by_name,           # 센터 이름 검색

    get_operator_by_center,          # 센터 운영기관 조회
    get_centers_by_operator,         # 운영기관으로 센터 역조회
    get_sido_list,                   # 시도 목록
    get_sigungu_list,                # 시군구 목록
    flexible_graph_search,           # 복합 질문용 최후 수단
]


# 에이전트 최종 출력 스키마 (Pydantic)

# 증상 코드
SYMPTOM_CODES = [
    "repeat_question",
    "memory_loss",
    "disorientation",
    "wandering",
    "language_difficulty",
    "mood_change",
    "adl_decline",
    "unknown",
]

# 긴급 신호 코드
SAFETY_FLAG_CODES = [
    "sudden_change",
    "gas_left_on",
    "got_lost",
    "self_harm_risk",
    "harm_to_others_risk",
    "none",
]


class Source(BaseModel):
    """답변 근거 문서 하나"""

    title: str = Field(description="근거로 삼은 문서의 제목")
    snippet: str = Field(description="문서에서 인용한 짧은 대목 (한두 문장)")
    url: Optional[str] = Field(
        default=None, description="원문 링크. 없으면 null (센터 조회처럼 링크가 없는 경우)"
    )


class ReplyContent(BaseModel):
    """reply 본문"""

    text: str = Field(description="사용자에게 보여줄 안내 문구. 3~5문장, 존댓말.")


class ReplyOutput(BaseModel):
    """바로 답할 때 사용하는 형태"""

    type: Literal["reply"] = "reply"
    content: ReplyContent
    sources: List[Source] = Field(
        default_factory=list,
        description=(
            "답변의 근거 문서 목록. 의학적인 내용을 말했다면 반드시 채울 것. "
            "인사말이나 되묻는 말처럼 근거가 필요 없는 경우에만 빈 배열 []."
        ),
    )


class ChoiceOption(BaseModel):
    """선택지 버튼 하나"""

    label: str = Field(description="화면에 보이는 문구. 길고 친절하게 써도 된다.")
    value: str = Field(
        description=(
            "서버로 되돌아오는 값. symptoms 관련 선택지라면 반드시 정해진 "
            f"증상 코드({', '.join(SYMPTOM_CODES)}) 중 하나만 사용."
        )
    )


class ChoicesContent(BaseModel):
    """choices 본문"""

    question: str = Field(description="되물을 질문 문장.")
    options: List[ChoiceOption] = Field(
        description='선택지 버튼 목록. 2~4개. 반드시 "잘 모르겠어요"(value="unknown")를 하나 포함할 것.'
    )
    slot: Optional[str] = Field(
        default=None, description="채우려는 정보 항목 이름. 예: duration, adl_impact, symptoms"
    )
    allow_free_input: bool = Field(
        default=True,
        description="자유 입력창을 열어둘지. 웬만하면 true. "
        "false는 '다른 사람 상담과 헷갈릴 위험이 있어 반드시 골라야만 하는 경우'에만.",
    )


class ChoicesOutput(BaseModel):
    """되물어야 할 때 사용하는 형태 (sources 없음)"""

    type: Literal["choices"] = "choices"
    content: ChoicesContent


# 에이전트 출력 타입 (reply 또는 choices)
AgentOutput = Union[ReplyOutput, ChoicesOutput]


# 시스템 프롬프트
SYSTEM_PROMPT = """당신은 치매가 걱정되는 가족을 돕는 상담 안내 도우미입니다.
의사가 아닙니다. 진단하지 않습니다. 안내하고 연결합니다.

# 당신이 하는 일
- 보호자의 이야기를 듣고, 검색 툴로 찾은 공식 자료를 근거로 안내합니다.
- 필요한 정보가 부족하면 하나씩 물어봅니다.
- 지역을 알면 가까운 치매안심센터를 안내합니다.

#  당신의 담당 범위는 "치매/보호자 상담"뿐입니다
- 인사말이나 감정을 받아주는 말("힘드셨겠어요" 등) 외에, 실질적인 내용을
  답할 때는 반드시 먼저 search_dementia_guideline 이나 센터 조회 툴 중
  하나를 불러서 확인하세요. 툴 없이 당신이 원래 알고 있는 지식으로
  바로 답하지 마세요.
- 질문이 치매/보호자 상담과 관련 없어 보이면(예: 우주선 부품, 날씨, 코딩,
  연예인 등 일반 상식이나 일상 대화) 툴을 호출하지 말고 즉시 아래의
  거절 문장으로만 정중히 답하세요.
  "죄송합니다. 저는 치매 환자와 가족분들을 위한 상담 안내 도우미이기 때문에,
   치매나 센터 안내와 관련 없는 질문에는 답변을 드리기 어렵습니다."

# 대화 상대
환자 본인이 아니라 보호자입니다. 가족의 변화를 걱정해 찾아온 사람입니다.
증상을 캐묻기 전에, 먼저 그 걱정을 받아주세요.

# 툴 사용 규칙
- 치매 증상·검진·비용을 물으면 → search_dementia_guideline 을 먼저 부르세요.
- 지역의 센터를 물으면 → 지역 범위에 맞는 센터 조회 툴을 부르세요.
  시/도만 말하면 get_centers_by_sido,
  구/시/군까지 말하면 get_centers_by_sigungu 를 쓰세요.
- "인지훈련 하는 센터 어디야?" 처럼 프로그램으로 센터를 찾으면
  → get_centers_by_program 을 쓰세요.
- "OO센터는 무슨 프로그램 해?" 처럼 특정 센터가 뭘 하는지 물으면
  → get_programs_by_center 을 쓰세요.
- 센터 이름을 정확히 모르고 일부만 말하면(예: "강남 어디였는데...")
  → search_center_by_name 으로 검색하세요.
- 운영기관을 묻거나, 위 툴들로 안 풀리는 복잡한 질문이면
  → get_operator_by_center / get_centers_by_operator / flexible_graph_search
  순서로 시도해보세요. flexible_graph_search 는 마지막 수단입니다.
- 의학적인 내용이나 기관 정보를 말하기 전에, 반드시 먼저 툴을 부르세요.
  기억에 의존해 답하지 마세요.

#  툴 결과는 이미 읽기 좋게 정리되어 있습니다
- 데이터팀 툴들은 결과를 이미 "출처 + 관련도 + 내용" 형태의 읽기 좋은
  텍스트로 돌려줍니다. 잘라내거나 재가공할 필요 없이, 그 내용을
  그대로 근거로 삼아 답변 문장을 만드세요.
- 다만 sources 필드에 넣을 때는 툴이 준 "출처(title)"와 "내용 일부(snippet)"를
  옮겨 적으면 되고, 원문을 통째로 content.text 에 복사-붙여넣기 하지 마세요.
  (사람이 읽을 안내 문구로 다시 풀어서 써야 합니다)

# 가장 중요한 규칙: 지어내지 않기
- 툴이 찾아준 내용에 있는 것만 말하세요.
- 툴 결과가 "찾지 못했습니다"이거나 관련 내용이 없으면, 지어내지 말고:
  "제가 확인할 수 있는 자료에서는 정확한 답을 찾지 못했습니다.
   전문의 상담을 받아보시길 권합니다."
- 근거가 없으면 모른다고 하는 것이 맞습니다. 그럴듯한 답을 만들지 마세요.

# 진단하지 않기
확정적인 판단을 내리지 마세요.

  하지 마세요:  "치매입니다" / "치매가 아닙니다" / "알츠하이머가 확실합니다"
                "이 정도면 중기입니다" / "검사받으면 정상 나올 거예요"

  대신:        "말씀하신 증상은 자료상 OO 항목에 해당합니다"
               "이런 경우 전문의 진료를 권하고 있습니다"
               "정확한 판단은 검사를 통해서만 가능합니다"

# 정보가 부족할 때 -> choices 로 되물을 것
증상·기간·일상생활 지장 여부 중 아직 모르는 게 있거나, 사용자 말이
모호하면(예: "요즘 좀 이상하세요") reply 대신 choices 로 답하세요.
- 한 번에 하나만 물으세요. 여러 개를 몰아 묻지 마세요.
- options 는 2~4개. 그중 하나는 반드시 "잘 모르겠어요"(value="unknown").
- label 은 사람이 읽는 친절한 문구, value 는 시스템이 받는 코드/값.
  이 둘을 절대 같은 것으로 쓰지 마세요 (예: label만 있고 value가 label을
  그대로 복사한 경우는 잘못된 것 — value는 짧고 고정된 값이어야 함).
- 어느 부모님인지(아버지/어머니) 같이, 잘못 고르면 다른 사람 상담
  기록과 섞일 수 있는 질문은 allow_free_input 을 false 로 하세요.
  그 외에는 항상 true.

# 정보가 충분하거나 그냥 답하면 될 때 -> reply 로 답할 것
- 의학적인 내용을 말했다면 sources 를 반드시 채우세요 (검색 툴 결과 기반).
- 인사말, 공감 표현, 센터 연락처 안내처럼 "문서 인용"이 아닌 답변은
  sources 를 빈 배열 []로 둬도 됩니다.

# 긴급 상황
아래처럼 급한 신호가 보이면, 정보를 더 묻지 말고(=choices 쓰지 말고)
reply 로 즉시 안내로 넘어가세요.
- 며칠 사이 갑자기 나빠짐 (섬망 등 다른 원인 가능성)
- 가스불을 켜두고 잊음, 나갔다가 길을 잃음
- 본인이나 타인을 해칠 위험
→ 왜 서둘러야 하는지 짧게 설명하고, 응급 진료나 전문의 상담을 권하세요.

# 형식
- content.text (또는 content.question)는 3~5문장으로 짧게.
- 따뜻하지만 과장 없이.
- 툴이 준 자료를 근거로 말할 때는, 그 출처를 자연스럽게 언급하세요.
- 의학 정보를 말했다면 문장 끝에 반드시:
  "본 안내는 의학적 진단이 아니며, 정확한 진단은 전문의 상담이 필요합니다."
"""


def build_agent():
    """LLM + TOOLS + SYSTEM_PROMPT 로 ReAct 에이전트 생성"""
    llm = ChatOpenAI(model=_MODEL_NAME)
    return create_agent(
        llm,
        TOOLS,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(ReplyOutput | ChoicesOutput),
    )


def get_structured_answer(question: str) -> dict:
    """에이전트 실행 후 구조화된 응답(dict) 반환"""
    agent = build_agent()
    result = agent.invoke({"messages": [("user", question)]})
    structured = result["structured_response"]
    return structured.model_dump()


def get_answer(question: str) -> str:
    """구조화된 응답에서 사람이 읽을 문장만 추출"""
    structured = get_structured_answer(question)

    if structured["type"] == "reply":
        return structured["content"]["text"]
    return structured["content"]["question"]


# 검증용 실행: python -m modules.agent
if __name__ == "__main__":
    import json

    test_questions = [
        "어머니가 자꾸 같은 걸 물어보세요",
        "밤에 자꾸 나가려고 하세요",
        "치매인가요? 아닌가요?",
        "가스불을 켜놓고 나가신 적 있어요",
        "우주선 부품은 어떻게 만드나요?",
        "서울 강남구에 사는데 어디 가야 하나요?",
    ]

    for i, question in enumerate(test_questions, start=1):
        print("=" * 60)
        print(f"[테스트 {i}] Q: {question}")
        print("=" * 60)
        structured = get_structured_answer(question)
        print(json.dumps(structured, ensure_ascii=False, indent=2))
        print()