# server/agent_new.py
from typing import List, Literal, Optional, Union, Sequence, Annotated
import operator
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
import json

from vector_db.vector_search_tool import search_dementia_guideline
from server.extractor import propose_state_change
from server.family_tool import query_family_info
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

_MODEL_NAME = "gemini-3.1-flash-lite"

TOOLS = [
    query_family_info,
    search_dementia_guideline,
    propose_state_change,
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
]

SYMPTOM_CODES = [
    "repeat_question", "memory_loss", "disorientation", "wandering",
    "language_difficulty", "mood_change", "adl_decline", "unknown",
]

SAFETY_FLAG_CODES = [
    "sudden_change", "gas_left_on", "got_lost", "self_harm_risk",
    "harm_to_others_risk", "none",
]

# =========================================================
# Step 1 Models (No Sources)
# =========================================================
class ReplyContent(BaseModel):
    text: str = Field(description="사용자에게 보여줄 안내 문구. 3~5문장, 존댓말.")

class AgentReplyOutput(BaseModel):
    """그냥 답하면 될 때 사용하는 형태. (출처 없이 텍스트만)"""
    type: Literal["reply"] = "reply"
    content: ReplyContent

class ChoiceOption(BaseModel):
    label: str = Field(description="화면에 보이는 문구. 길고 친절하게 써도 된다.")
    value: str = Field(
        description=f"서버로 되돌아오는 값. symptoms 관련 선택지라면 반드시 정해진 증상 코드({', '.join(SYMPTOM_CODES)}) 중 하나만 사용."
    )

class ChoicesContent(BaseModel):
    question: str = Field(description="되물을 질문 문장.")
    options: List[ChoiceOption] = Field(
        description='선택지 버튼 목록. 2~4개. 반드시 "잘 모르겠어요"(value="unknown")를 하나 포함할 것.'
    )
    slot: Optional[str] = Field(default=None, description="채우려는 정보 항목 이름.")
    allow_free_input: bool = Field(default=True, description="자유 입력창을 열어둘지.")

class ChoicesOutput(BaseModel):
    type: Literal["choices"] = "choices"
    content: ChoicesContent

AgentOutput = Union[AgentReplyOutput, ChoicesOutput]

# =========================================================
# Step 2 Models (Sources)
# =========================================================
class Source(BaseModel):
    title: str = Field(description="근거로 삼은 문서나 센터의 제목")
    snippet: str = Field(description="문서에서 인용한 짧은 대목 (한두 문장)")
    url: Optional[str] = Field(
        default=None, description="원문 홈페이지 링크. 홈페이지 정보가 없거나 비어있으면 아예 url 필드를 포함하지 마세요."
    )

class ExtractedSources(BaseModel):
    sources: List[Source] = Field(
        default_factory=list,
        description="추출된 출처 목록. 근거가 필요 없는 일반 텍스트일 경우 빈 배열 []."
    )

class FinalReplyOutput(BaseModel):
    type: Literal["reply"] = "reply"
    content: ReplyContent
    sources: List[Source] = Field(default_factory=list)

# =========================================================
# Prompts
# =========================================================
SYSTEM_PROMPT = """당신은 치매가 걱정되는 가족을 돕는 상담 안내 도우미입니다.
의사가 아닙니다. 진단하지 않습니다. 안내하고 연결합니다.

# 당신이 하는 일
- 보호자의 이야기를 듣고, 검색 툴로 찾은 공식 자료를 근거로 안내합니다.
- 한 번에 너무 많은 것을 묻지 말고, 가장 중요한 정보 하나씩만 물어봅니다.
- [중요] 대화 목적에 맞는 질문만 하세요. 증상이나 상태 상담이 목적일 때는 관련된 행동 변화나 증상만 물어보고, 뜬금없이 거주 지역을 묻지 마세요.
- [중요] 센터 방문을 권유할 단계가 되었거나 사용자가 기관 안내를 원할 때만 거주 지역(시/군/구)을 물어보세요.

# 당신의 담당 범위는 "치매/보호자 상담"뿐입니다
- 질문이 전혀 관련 없어 보이면 거절 문장으로 답하세요.
- 다만 이전 대화 기록에 나온 사용자의 정보는 기억하고 활용하세요.

# 툴 사용 규칙
- 의학적인 내용이나 기관 정보를 말하기 전에 반드시 먼저 툴을 부르세요.
- 툴 결과는 이미 읽기 좋게 정리되어 있습니다. 그대로 근거로 삼아 답변 문장을 만드세요.

# 가장 중요한 규칙: 지어내지 않기
- 치매 증상, 의학 정보, 센터 정보 등 사실 확인이 필요한 내용을 말할 때는 반드시 툴이 찾아준 내용에 있는 것만 말하세요.
- 툴 결과가 "찾지 못했습니다"이거나 관련 내용이 없으면 지어내지 말고 전문의 상담을 권하세요.

# 1인칭 발화는 누구 얘기인지 먼저 확인할 것
"나 집가고싶어", "나 배고파" 처럼 1인칭("나 ~", "저 ~")으로 오는 발화는
- (a) 환자가 실제로 한 말을 보호자가 그대로 전달한 것일 수도 있고
- (b) 보호자 본인의 감정/상태 표현일 수도 있습니다.
"~라고 하세요", "~하신대요" 같은 인용 표현이 없으면 둘 중 뭔지 확정할
근거가 없는 것입니다. 이럴 때는 절대 짐작해서 검색하거나 답하지 말고,
아래처럼 choices 로 먼저 확인하세요:
  question: "방금 말씀은 어르신이 하신 말씀인가요, 보호자님 마음이신가요?"
  options: [
    {label: "어르신이 그렇게 말씀하셨어요", value: "patient_quote"},
    {label: "제 마음이 그래요", value: "self_expression"}
  ]
  allow_free_input: true

# 진단하지 않기
확정적인 판단(치매입니다 등)을 내리지 마세요.

# 정보가 부족해서 되물을 때의 규칙 (질문 유형에 따라 분기)
1. [주관식/개방형] 직접 입력이 필요한 경우 -> `reply` 형식 사용
   - "거주하시는 지역(시/군/구)이 어디신가요?", "어머니의 증상을 구체적으로 말씀해 주시겠어요?" 등 사용자가 주관식으로 답변해야 하는 개방형 질문일 때는 절대 억지로 선택지(choices)를 만들지 마세요.
   - 이때는 일반적인 `reply` 형식을 사용하여 질문 텍스트만 출력하세요. (사용자가 하단 입력창에 직접 타이핑하여 대답할 것입니다.)

2. [객관식/폐쇄형] 선택지가 적합한 경우 -> `choices` 형식 사용
   - 증상 유형, 빈도, 위험 상황 유무 등 정해진 보기 중에서 고르는 것이 편한 질문일 때만 `choices` 형식을 사용하세요.
   - options 중 하나는 반드시 "잘 모르겠어요"(value="unknown")를 포함하세요.
   - [중요] options의 label은 사용자가 클릭할 버튼이므로 사용자 입장의 '평서문(대답)' 형식으로 작성하세요. 절대로 AI의 질문 형식(예: "최근에 깜빡하시나요?")으로 작성하지 마세요. (올바른 예: "네, 최근 들어 깜빡하세요", "예전부터 그랬어요")
   - [중요] 만약 사용자가 이전 대화에서 "잘 모르겠어요"라고 대답했다면, 대화를 포기하고 즉시 안내(reply)로 종료하지 마세요. 대신 사용자가 더 쉽게 대답할 수 있는 구체적이고 단순한 우회 질문(choices 또는 reply)을 다시 던져 대화를 이어가세요.

# 긴급 상황
며칠 사이 갑자기 나빠짐, 가스불 켜둠, 본인/타인 해칠 위험 등이 보이면 즉시 안내(reply)로 넘어가세요.
"""

SOURCE_EXTRACTION_PROMPT = """당신은 앞서 생성된 답변의 출처를 찾아내는 보조 에이전트입니다.
제공된 '도구 실행 결과(Tool Messages)'와 '생성된 답변 텍스트(Reply Text)'를 보고,
답변의 근거가 된 출처(문서나 센터 정보)를 정확하게 추출하여 JSON 배열로 만드세요.

규칙:
1. 반드시 도구 실행 결과에 있는 내용만 출처로 사용하세요.
2. 도구 결과에 없는 임의의 URL이나 제목을 지어내지 마세요.
3. 센터 정보의 경우, 도구 결과에 '홈페이지: ...' 로 명시된 링크가 있으면 넣고, 없으면 url 필드를 아예 생략하세요.
4. 인사말, 공감, 단순 안내 등 근거가 전혀 필요 없는 텍스트라면 빈 배열 [] 을 반환하세요.
5. '운영기관'과 관련된 내용은 출처 목록에서 완전히 제외하세요.
6. 동일한 문서나 센터(특히 같은 URL을 가진 경우)에 대해 중복된 출처를 절대 생성하지 마세요. 대표적인 1개만 남기세요.
"""

CHOICES_REWRITE_PROMPT = """당신은 치매 상담 챗봇의 보조 에이전트입니다.
AI가 정보를 추가로 얻기 위해 사용자에게 질문(choices)을 던지려고 합니다.
제공된 '이전 대화 내역'과 'AI가 방금 생성한 질문'을 보고, 이 질문이 너무 기계적이거나, 사용자가 방금 말한 내용을 앵무새처럼 반복하는 어색한 질문인지 검사하세요.

규칙:
1. 만약 사용자의 직전 대답과 AI의 새 질문이 거의 똑같다면(예: 사용자: "최근에 더 나빠지셨나요?", AI: "최근에 더 나빠지셨나요?"), 직전 대답에 자연스럽게 공감하며 넘어가는 부드러운 대화체로 수정하세요. (예: "어머니께서 최근 갑자기 나빠지셨군요. 그렇다면...")
2. 질문이 이미 자연스럽다면 굳이 수정하지 말고 그대로 반환하세요.
3. 수정된 '질문 문자열(String)' 하나만 반환하세요.
"""

class RewrittenQuestion(BaseModel):
    question: str = Field(description="자연스럽게 수정된 질문 문자열")

# =========================================================
# Graph State
# =========================================================
class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    structured_response: Optional[AgentOutput]
    final_response: Optional[Union[FinalReplyOutput, ChoicesOutput]]

# =========================================================
# Nodes
# =========================================================
_agent_instance = None

def get_base_agent():
    global _agent_instance
    if _agent_instance is None:
        llm = ChatGoogleGenerativeAI(model=_MODEL_NAME)
        _agent_instance = create_agent(
            llm,
            TOOLS,
            system_prompt=SYSTEM_PROMPT,
            response_format=ToolStrategy(AgentReplyOutput | ChoicesOutput),
        )
    return _agent_instance

def generation_node(state: GraphState):
    agent = get_base_agent()
    # pass all previous messages
    result = agent.invoke({"messages": state["messages"]})
    
    return {
        "messages": result["messages"], # Contains human, ai, and tool messages
        "structured_response": result["structured_response"]
    }

def source_extraction_node(state: GraphState):
    response = state["structured_response"]
    
    # Extract Tool messages
    tool_messages = [msg.content for msg in state["messages"] if isinstance(msg, ToolMessage)]
    tool_context = "\n---\n".join(tool_messages) if tool_messages else "No tools were called."
    reply_text = response.content.text
    
    llm = ChatGoogleGenerativeAI(model=_MODEL_NAME).with_structured_output(ExtractedSources)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", SOURCE_EXTRACTION_PROMPT),
        ("user", "도구 실행 결과:\n{tool_context}\n\n생성된 답변:\n{reply_text}")
    ])
    
    chain = prompt | llm
    extracted = chain.invoke({
        "tool_context": tool_context,
        "reply_text": reply_text
    })
    
    # 파이썬 로직 차원의 중복 제거 (URL 기준, 없으면 제목 기준)
    unique_sources = []
    seen_identifiers = set()
    for src in extracted.sources:
        identifier = src.url if src.url else src.title
        if identifier not in seen_identifiers:
            unique_sources.append(src)
            seen_identifiers.add(identifier)
            
    # Create final response combining text and sources
    final_response = FinalReplyOutput(
        type="reply",
        content=response.content,
        sources=unique_sources
    )
    
    return {
        "final_response": final_response
    }

def choices_rewrite_node(state: GraphState):
    response = state["structured_response"]
    
    if getattr(response, "type", None) == "choices":
        original_question = response.content.question
        
        chat_history = []
        for msg in state["messages"]:
            if isinstance(msg, HumanMessage):
                chat_history.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
                chat_history.append(f"AI: {msg.content}")
        
        history_text = "\n".join(chat_history[-4:])
        
        llm = ChatGoogleGenerativeAI(model=_MODEL_NAME).with_structured_output(RewrittenQuestion)
        prompt = ChatPromptTemplate.from_messages([
            ("system", CHOICES_REWRITE_PROMPT),
            ("user", "이전 대화 내역:\n{history_text}\n\nAI가 생성한 질문:\n{original_question}")
        ])
        
        chain = prompt | llm
        try:
            rewritten = chain.invoke({
                "history_text": history_text,
                "original_question": original_question
            })
            response.content.question = rewritten.question
        except Exception as e:
            pass # 보조 LLM 실패 시 원본 질문 유지

    return {
        "final_response": response
    }

def route_after_generation(state: GraphState):
    response = state["structured_response"]
    if getattr(response, "type", None) == "reply":
        return "source_extraction"
    return "choices_rewrite"

# =========================================================
# Graph Compilation
# =========================================================
_graph_instance = None

def build_agent():
    global _graph_instance
    if _graph_instance is None:
        builder = StateGraph(GraphState)
        builder.add_node("generation", generation_node)
        builder.add_node("source_extraction", source_extraction_node)
        builder.add_node("choices_rewrite", choices_rewrite_node)
        
        builder.add_edge(START, "generation")
        builder.add_conditional_edges(
            "generation",
            route_after_generation,
            {
                "source_extraction": "source_extraction",
                "choices_rewrite": "choices_rewrite"
            }
        )
        builder.add_edge("source_extraction", END)
        builder.add_edge("choices_rewrite", END)
        
        _graph_instance = builder.compile()
    return _graph_instance

# =========================================================
# API Interfaces
# =========================================================
def get_structured_answer(question: str) -> dict:
    graph = build_agent()
    result = graph.invoke({
        "messages": [HumanMessage(content=question)],
        "structured_response": None,
        "final_response": None
    })
    
    final_resp = result["final_response"]
    # Pydantic model dump
    return final_resp.model_dump()

def get_answer(question: str) -> str:
    structured = get_structured_answer(question)
    if structured["type"] == "reply":
        return structured["content"]["text"]
    return structured["content"]["question"]

if __name__ == "__main__":
    test_questions = [
        "어머니가 자꾸 같은 걸 물어보세요",
        "밤에 자꾸 나가려고 하세요",
        "치매인가요? 아닌가요?",
        "가스불을 켜놓고 나가신 적 있어요",
        "우주선 부품은 어떻게 만드나요?",
        "서울 강남구에 사는데 어디 가야 하나요?",
        "나 집가고 싶어"
    ]

    for i, question in enumerate(test_questions, start=1):
        print("=" * 60)
        print(f"[테스트 {i}] Q: {question}")
        print("=" * 60)
        structured = get_structured_answer(question)
        print(json.dumps(structured, ensure_ascii=False, indent=2))
        print()
