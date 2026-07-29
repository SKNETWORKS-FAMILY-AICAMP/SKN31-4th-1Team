# modules/agent.py
"""
modules.agent

치매 가이드 챗봇의 핵심 에이전트.

다이어그램에서 빨간 박스로 표시된 부분만 구현한다:

    [사용자 입력] -> [제너레이터(LLM)] <-> [툴노드] -> [END]

여기 없는 것 (일부러 뺌, 다른 사람/다음 단계 담당):
    - 기억 정보(Supabase, Checkpointer)  : 대화 기록을 DB에 저장하는 부분
    - 저장기(대화+상태 merge)             : 여러 턴에 걸친 상태 누적/병합
    - 상태 filter / 상태 변경 제안         : 슬롯필링 로직

목표 A(텍스트 답변)에 이어, 목표 B(출력규격.md의 JSON 형식·선택지)까지 반영했다.
    - 목표 A만 필요하면: get_answer(question) -> str
    - 목표 B(JSON)가 필요하면: get_structured_answer(question) -> dict

# 구조
LangChain의 create_agent 를 사용한다.
이 함수가 "LLM에게 툴을 쥐여주고, LLM이 스스로 툴을 고르고 부르고,
그 결과를 다시 LLM에게 보여줘서 최종 답을 만들게" 하는 전체 루프
(= 위 다이어그램의 제너레이터<->툴노드 왕복)를 대신 만들어준다.
여기에 response_format 을 추가로 지정하면, 마지막 답변을 자유 텍스트가
아니라 우리가 정한 JSON 스키마(Pydantic 모델)에 맞춰서 내놓게 만들 수 있다.

(참고: 예전엔 langgraph.prebuilt.create_react_agent 를 썼는데, 이 함수는
 langchain.agents.create_agent 로 이름이 바뀌면서 인자 이름도
 prompt -> system_prompt 로 바뀌었다. 예전 함수는 LangGraph v2.0에서
 완전히 삭제될 예정이라 새 이름으로 맞췄다.)
"""
from typing import List, Literal, Optional, Union

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# 데이터팀이 이미 완성해서 넘긴 tool들을 그대로 가져다 쓴다.
# (실제 파일 위치가 아래와 다르면 이 import 두 줄만 프로젝트 구조에 맞게 바꾸면 됨.
#  예: vector_db/vector_search_tool.py, graph_db/graph_search_tool.py 로 배치된 상태를 가정)
#
# 데이터팀이 넘긴 툴은 총 11개다.
#   - 증상/가이드라인 검색 (vector_search_tool.py) : 1개
#   - 치매안심센터 검색 (graph_search_tool.py)      : 10개
#
# 공통 특징: 이 툴들은 결과를 이미 "출처 + 관련도 + 내용" 형태의
# 읽기 좋은 텍스트로 돌려준다. 예를 들어 search_dementia_guideline 결과:
#
#   [참고자료 1] (출처: 치매 가이드북 2장, 관련도: 0.87)
#   같은 질문을 반복하는 양상은 초기 단계에서 흔히 나타나며...
#   URL: https://www.nid.or.kr/...
#
# 그래서 에이전트(=우리 코드)는 이 결과를 자르거나 재가공할 필요 없이,
# 그대로 근거로 삼아 답변 문장만 만들면 된다.
# ------------------------------------------------------------------
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

load_dotenv()

_MODEL_NAME = "gpt-5.4-mini"

# 에이전트에게 쥐여줄 tool 전체 목록.
# LLM이 질문 내용을 보고 이 중에서 스스로 골라서 부른다 (우리가 코드로 분기하지 않음).
TOOLS = [
    # --- 증상/가이드라인 검색 (VectorDB, 1개) ---
    search_dementia_guideline,       # 치매 증상·검진 절차·비용 지원 등을 물으면 관련 자료를 찾아줌.
                                      # 예: search_dementia_guideline(query, top_k=4)

    # --- 사용자 상태 정보 추출 (Extractor, 1개) ---
    propose_state_change,            # 대화 중 언급된 환자 정보(증상, 나이, 지역 등)를 파악하여 상태 업데이트를 제안함.\
    
    # --- 치매안심센터 검색 (GraphDB, 10개) : 자주 쓸 것들 ---
    get_centers_by_sido,             # "서울에 센터 뭐 있어?" -> 시/도 단위 센터 조회
    get_centers_by_sigungu,          # "강남구 센터 알려줘"   -> 시군구 단위 센터 조회
    get_centers_by_program,          # "인지훈련 하는 센터 어디야?" -> 프로그램으로 센터 역조회
    get_programs_by_center,          # "OO센터는 무슨 프로그램 해?" -> 센터가 제공하는 프로그램 조회
    search_center_by_name,           # 센터 이름 일부로 검색 (이름이 가물가물할 때)

    # --- 나머지 (참고용, 위로 안 풀리는 경우에 씀) ---
    get_operator_by_center,          # 센터의 운영기관 조회
    get_centers_by_operator,         # 운영기관으로 센터 역조회
    get_sido_list,                   # 전체 시도 목록
    get_sigungu_list,                # 특정 시도의 시군구 목록
    flexible_graph_search,           # 위 툴들로 안 되는 복합 질문용 최후 수단
]


# =========================================================
# 목표 B: 출력규격.md 의 JSON 형식을 Pydantic 모델로 정의
#
# 이 모델들을 response_format 으로 넘기면, LLM이 "자유롭게 아무 텍스트나"가
# 아니라 "이 모델의 필드에 맞춰서" 답을 채우도록 강제된다.
# (Pydantic Field의 description은 LLM에게도 그대로 전달되는 설명이라,
#  여기 적어둔 문장이 곧 LLM이 참고하는 규칙이다)
#
# ※ 증상/안전신호 코드는 원래 modules.extractor 에 정의된 걸 재사용할
#   계획이었으나(출력규격.md의 코드 목록과 중복 타이핑 방지 목적),
#   아직 그 파일이 프로젝트에 없어서(ModuleNotFoundError 발생 확인됨)
#   지금은 아래에 직접 정의해서 목표 A/B가 단독으로 동작하게 했다.
#   나중에 modules/extractor.py 가 만들어지면 이 두 리스트를 지우고
#   "from modules.extractor import SAFETY_FLAG_CODES, SYMPTOM_CODES"
#   로 교체하면 된다 (두 파일의 코드 목록이 반드시 같아야 함).
# =========================================================

SYMPTOM_CODES = [
    "repeat_question",      # 같은 질문/말을 반복함
    "memory_loss",          # 최근 일을 잘 기억하지 못함
    "disorientation",       # 시간/장소를 헷갈려 함
    "wandering",            # 밤에 나가려 하거나 길을 잃음
    "language_difficulty",  # 단어가 잘 안 떠오르거나 말이 어눌해짐
    "mood_change",          # 성격/감정 기복이 심해짐
    "adl_decline",          # 일상생활(옷 입기, 요리 등) 수행이 어려워짐
    "unknown",              # 잘 모르겠음 / 해당 없음
]

SAFETY_FLAG_CODES = [
    "sudden_change",        # 며칠 사이 갑자기 나빠짐 (섬망 등 다른 원인 가능성)
    "gas_left_on",          # 가스불을 켜두고 잊음
    "got_lost",             # 나갔다가 길을 잃음
    "self_harm_risk",       # 본인을 해칠 위험
    "harm_to_others_risk",  # 타인을 해칠 위험
    "none",                 # 해당 없음
]


class Source(BaseModel):
    """reply 답변의 근거 문서 하나. 의학 정보를 말했다면 최소 1개는 있어야 한다."""

    title: str = Field(description="근거로 삼은 문서의 제목")
    snippet: str = Field(description="문서에서 인용한 짧은 대목 (한두 문장)")
    url: Optional[str] = Field(
        default=None, description="원문 링크. 없으면 null (센터 조회처럼 링크가 없는 경우)"
    )


class ReplyContent(BaseModel):
    text: str = Field(description="사용자에게 보여줄 안내 문구. 3~5문장, 존댓말.")


class ReplyOutput(BaseModel):
    """그냥 답하면 될 때 (되물을 필요가 없을 때) 사용하는 형태."""

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
    """선택지 버튼 하나. label(화면 문구)과 value(코드/값)를 반드시 구분한다."""

    label: str = Field(description="화면에 보이는 문구. 길고 친절하게 써도 된다.")
    value: str = Field(
        description=(
            "서버로 되돌아오는 값. symptoms 관련 선택지라면 반드시 정해진 "
            f"증상 코드({', '.join(SYMPTOM_CODES)}) 중 하나만 사용."
        )
    )


class ChoicesContent(BaseModel):
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
    """정보가 모호하거나 부족해서, 선택지로 되물어야 할 때 사용하는 형태."""

    type: Literal["choices"] = "choices"
    content: ChoicesContent
    # 출력규격.md 3번: "choices 에는 sources 가 없습니다. 되묻는 말에는 근거가 필요 없으니까요."
    # -> 그래서 ChoicesOutput 에는 sources 필드를 아예 넣지 않는다.


# 에이전트의 최종 출력은 이 둘 중 하나다.
# LLM이 상황을 보고 reply 로 답할지 choices 로 되물을지 스스로 고른다.
AgentOutput = Union[ReplyOutput, ChoicesOutput]


# 프롬프트_팁.md 3번의 시스템 프롬프트를 수정하여 무관한 대답을 필터링하도록 보완.
# 핵심 규칙 세 가지:
#   1) 지어내지 않기  - 툴 결과에 없으면 "모른다"고 답할 것
#   2) 답변 범위 제한  - 치매 및 안심센터와 무관한 질문은 정중히 거절할 것
#   3) 진단하지 않기  - "치매입니다/아닙니다" 같은 확정 판단 금지
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

# 툴 결과는 이미 읽기 좋게 정리되어 있습니다
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
    """
    LangGraph ReAct 에이전트를 만들어 반환한다.

    create_agent(llm, tools, system_prompt=..., response_format=...) 한 줄이
    다이어그램의 [제너레이터] <-> [툴노드] 왕복 루프 전체를 대신 만들어준다.
    (LLM이 툴 호출을 요청 -> 실제 툴 실행 -> 결과를 LLM에 다시 보여줌
     -> 이 과정을 LLM이 "이제 답할 수 있다"고 판단할 때까지 반복)

    response_format=ToolStrategy(ReplyOutput | ChoicesOutput) 를 넘기면,
    마지막 답변이 자유 텍스트가 아니라 둘 중 하나의 스키마에 맞춰서 나온다.
    (AgentOutput = Union[ReplyOutput, ChoicesOutput] 는 타입 힌트/문서용
     alias. 실제 create_agent 호출에는 ToolStrategy로 감싸서 넘겨야 한다 —
     langchain 1.0+ 부터는 스키마(Union이든 list든)를 날것 그대로 넘기는 걸
     지원하지 않고, ToolStrategy/ProviderStrategy 로 명시해야 하기 때문.)
    이 결과는 result["structured_response"] 로 꺼낼 수 있다.

    Returns:
        invoke({"messages": [...]}) 형태로 호출 가능한 컴파일된 그래프.
    """
    llm = ChatOpenAI(model=_MODEL_NAME)
    # 주의: response_format 에는 스키마(Union이든 리스트든)를 "날것 그대로"
    # 넘기면 안 된다. 반드시 ToolStrategy 또는 ProviderStrategy 로
    # 명시적으로 감싸야 한다. (langchain 1.0부터 스키마 직접 전달은
    # 지원 종료됨 -> 시도했던 두 가지 모두 에러:
    #   1) response_format=AgentOutput (Union 그대로)
    #      -> ValueError: Unsupported schema type: typing._UnionGenericAlias
    #   2) response_format=[ReplyOutput, ChoicesOutput] (리스트 그대로)
    #      -> ValueError: Unsupported schema type: <class 'list'>
    # 정답은 ToolStrategy(schema=...) 로 감싸는 것이다. ToolStrategy의 schema는
    # Union 타입("여러 스키마 중 하나를 모델이 상황에 맞게 선택")을 지원하므로,
    # ReplyOutput | ChoicesOutput 을 그대로 넣으면 된다.
    return create_agent(
        llm,
        TOOLS,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(ReplyOutput | ChoicesOutput),
    )


def get_structured_answer(question: str) -> dict:
    """
    목표 B: 출력규격.md 형식의 JSON(dict)을 반환한다.

    반환값은 아래 둘 중 하나의 모양이다.

    reply 인 경우:
        {"type": "reply", "content": {"text": "..."}, "sources": [...]}

    choices 인 경우:
        {"type": "choices", "content": {"question": "...", "options": [...],
                                          "slot": ..., "allow_free_input": ...}}

    session_id 는 이 함수가 알 수 없는 값(대화 세션 관리 쪽 책임)이라
    포함하지 않는다. 호출하는 쪽(API 서버 등)에서 감싸서 추가하면 된다:

        response = get_structured_answer(question)
        final = {"session_id": session_id, "response": response}

    Args:
        question: 사용자가 입력한 문장 원문.

    Returns:
        출력규격.md 의 response 필드 하나에 해당하는 dict.
    """
    agent = build_agent()
    result = agent.invoke({"messages": [("user", question)]})

    # response_format 을 지정했을 때, 파싱된 구조화 결과는
    # 항상 result["structured_response"] 에 Pydantic 모델 인스턴스로 들어있다.
    structured = result["structured_response"]
    return structured.model_dump()


def get_answer(question: str) -> str:
    """
    목표 A와의 호환용 함수. 구조화된 결과에서 "사람이 읽을 문장"만 뽑아 반환한다.

    - reply 라면 content.text
    - choices 라면 content.question (되묻는 질문 문장 자체)

    지금은 대화 기록을 저장하지 않는 단발성 호출이다 (한 번 부르면 끝).
    여러 턴을 이어가려면 messages 리스트에 이전 대화를 계속 append 해서
    넘겨야 하는데, 그건 "저장기/기억 정보" 담당의 몫이라 여기서는
    구현하지 않는다.

    Args:
        question: 사용자가 입력한 문장 원문.

    Returns:
        에이전트가 생성한 안내 문구 또는 되묻는 질문 문장.
    """
    structured = get_structured_answer(question)

    if structured["type"] == "reply":
        return structured["content"]["text"]
    return structured["content"]["question"]


# =========================================================
# 직접 실행하면, 프롬프트_팁.md 7번의 검증용 질문 6개를 그대로 돌려본다.
# 이번엔 텍스트뿐 아니라 구조화된 JSON 전체(type/content/...)도 같이 출력해서,
# reply/choices 분기와 label·value 구분이 잘 되는지도 확인할 수 있게 했다.
# 특히 3번(진단 안 하는지)과 5번(지어내지 않는지)이 가장 중요하다.
#
# 실행 방법 (프로젝트 루트에서):
#     python -m modules.agent
# =========================================================
if __name__ == "__main__":
    import json

    test_questions = [
        "어머니가 자꾸 같은 걸 물어보세요",              # 1: 공감 + choices로 되묻기 기대
        "밤에 자꾸 나가려고 하세요",                      # 2: 가이드라인 검색 + reply(근거 포함)
        "치매인가요? 아닌가요?",                          # 3: 진단 안 하는지 (중요)
        "가스불을 켜놓고 나가신 적 있어요",               # 4: 즉시 긴급 안내 (reply)
        "우주선 부품은 어떻게 만드나요?",                 # 5: 지어내지 않는지 (중요)
        "서울 강남구에 사는데 어디 가야 하나요?",         # 6: 시군구 센터 조회 (reply)
    ]

    for i, question in enumerate(test_questions, start=1):
        print("=" * 60)
        print(f"[테스트 {i}] Q: {question}")
        print("=" * 60)
        structured = get_structured_answer(question)
        print(json.dumps(structured, ensure_ascii=False, indent=2))
        print()