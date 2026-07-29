"""
치매안심센터 GraphDB 조회 툴 (Agent Tool)

에이전트가 "지역/운영기관/프로그램 기준으로 센터 정보가 필요하다"고 판단했을 때
호출하는 툴 모음. 총 10개 tool로 구성된다.

- 앞 9개: Neo4j AuraDB에 이미 검증된 Cypher 쿼리를 함수 안에 고정해두고,
  LLM은 "어떤 함수를 어떤 인자로 호출할지"만 결정한다. 관계 방향 오류나
  결과 개수 누락(top_k 잘림) 문제가 원천적으로 없다.
- 마지막 1개(flexible_graph_search): 앞의 9개로 커버되지 않는 예외적/복합
  질문을 위한 fallback. 이 tool 안에서만 GraphCypherQAChain(LLM이 매번
  Cypher를 즉석 생성하는 방식)을 사용한다. docstring에 "최후의 수단"임을
  명시해 자주 나오는 질문은 앞 9개가 우선 처리하도록 유도한다.

그래프 스키마 요약 (방향 전부 단방향, 아래가 전부):
    (:치매안심센터)-[:LOCATED_IN]->(:시군구)
    (:시군구)-[:LOCATED_IN]->(:시도)
    (:시도)-[:CONTAINS]->(:시군구)
    (:운영기관)-[:MANAGES]->(:치매안심센터)
    (:치매안심센터)-[:PROVIDES]->(:프로그램)

노드 식별 프로퍼티는 전부 name으로 통일되어 있다
(:시도, :시군구, :치매안심센터, :운영기관, :프로그램 전부 name).

프로퍼티 타입 요약 (:치매안심센터 기준, fallback의 cypher_prompt에도 동일하게 명시됨):
    int    : 의사인원수, 간호사인원수, 사회복지사인원수, 인원_작업치료사,
             인원_운동치료사, 인원_임상심리사, 인원_음악치료사, 인원_행정인력,
             인원_송영인력, 인원_기타
    float  : 경도, 위도 (위치 정밀도이므로 반올림/정수화 금지)
    string : 전화번호, 팩스번호, 홈페이지, 주소, 유형, 우편번호, name
             (우편번호는 앞자리 0이 유효한 값(예: 06153)이라 int가 아닌
             5자리 zero-pad 문자열로 저장됨. 비교 시 따옴표로 감싸야 함)
             (:운영기관)의 홈페이지/대표자명/전화번호와
             (:시도, :시군구, :운영기관, :프로그램)의 name도 전부 string.
    주의: 인원수 필드의 0은 "해당 인력 미배정"을 뜻하는 정상 값이며
          NULL(데이터 없음)과 다르다.

LangChain의 @tool 데코레이터를 사용해 LangGraph 에이전트(ToolNode 등)에
그대로 bind 할 수 있도록 구성.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_core.prompts import PromptTemplate
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase

# 이 파일이 어디서 import/실행되든(노트북이 프로젝트 루트에 있든, graph_db/
# 안에서 직접 실행하든) 항상 이 파일과 같은 폴더 기준 상대경로로 .env를 찾는다.
# (cwd 기준으로 찾으면 실행 위치에 따라 .env를 못 찾는 문제가 있었음)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, "..", ".env"))

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE")

FALLBACK_MODEL = "gpt-5.4-mini"  # flexible_graph_search 전용 LLM

RESULT_LIMIT_DEFAULT = 200  # GraphCypherQAChain의 top_k=10 잘림 문제 재발 방지용 넉넉한 기본값


# ------------------------------------------------------------
# 드라이버는 프로세스당 한 번만 로드 (lru_cache로 재사용)
# ------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_driver() -> GraphDatabase.driver:
    if not NEO4J_URI or not NEO4J_USERNAME or not NEO4J_PASSWORD:
        raise EnvironmentError(
            "NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD가 설정되어 있지 않습니다. .env 파일을 확인하세요."
        )
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


def _run_query(cypher: str, **params) -> list[dict]:
    """Cypher를 실행하고 결과를 dict 리스트로 반환하는 내부 헬퍼.

    @tool이 부착되지 않은 순수 실행 함수. 개별 조회 함수들이 이 함수를
    거쳐서 세션 생성/종료를 매번 반복하지 않도록 공통화한다.

    Args:
        cypher: 실행할 Cypher 쿼리 문자열
        **params: Cypher 쿼리에 바인딩할 파라미터

    Returns:
        각 레코드를 dict로 변환한 리스트
    """
    driver = _get_driver()
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, **params)
        return [record.data() for record in result]


def _run_query_capped(cypher: str, limit: int = RESULT_LIMIT_DEFAULT, **params) -> tuple[list[dict], bool]:
    """LIMIT으로 잘린 결과인지 여부까지 함께 알려주는 _run_query 래퍼.

    쿼리에 실제로는 limit보다 1 많은 개수를 요청해서, 반환된 행 수가
    limit을 넘으면 "잘렸다"는 뜻이므로 truncated=True로 표시하고 다시
    limit개로 잘라낸다. 이렇게 하면 "결과가 정확히 limit개라 안 잘림"과
    "더 있는데 limit에서 잘림"을 구분할 수 있다.

    Args:
        cypher: 실행할 Cypher 쿼리 문자열. 내부에 `LIMIT $limit`을 포함해야 한다.
        limit: 최종적으로 반환할 최대 개수
        **params: Cypher 쿼리에 바인딩할 나머지 파라미터

    Returns:
        (limit개로 잘라낸 결과 리스트, 잘렸는지 여부) 튜플
    """
    rows = _run_query(cypher, limit=limit + 1, **params)
    truncated = len(rows) > limit
    return rows[:limit], truncated


def _format_centers(rows: list[dict], truncated: bool = False, limit: int = RESULT_LIMIT_DEFAULT) -> str:
    """센터 조회 결과(dict 리스트)를 LLM이 읽기 좋은 텍스트 블록으로 정리.

    Args:
        rows: {'name', '주소', '전화번호', ...} 형태의 dict 리스트
        truncated: 실제 결과가 limit보다 많아서 잘렸는지 여부
        limit: 표시된 최대 개수 (잘림 안내 문구에 사용)

    Returns:
        사람이 읽기 좋은 텍스트. 결과가 없으면 그 사실을 명시한 문자열.
        잘렸을 경우 마지막에 안내 문구를 덧붙인다.
    """
    if not rows:
        return "조건에 맞는 치매안심센터를 찾지 못했습니다."

    blocks = []
    for i, r in enumerate(rows, start=1):
        name = r.get("name", "이름 없음")
        addr = r.get("주소", "")
        phone = r.get("전화번호", "")
        website = r.get("홈페이지", "")
        line = f"[{i}] {name} | 주소: {addr} | 전화번호: {phone}"
        if website:
            line += f" | 홈페이지: {website}"
        blocks.append(line)
    text = "\n".join(blocks)
    if truncated:
        text += f"\n\n(조건에 맞는 센터가 {limit}개보다 많아 상위 {limit}개만 표시했습니다. 지역을 더 좁혀서 다시 물어보시면 전체를 확인하실 수 있습니다.)"
    return text


# ==============================================================
# 1. 지역 기준 조회
# ==============================================================
@tool
def get_centers_by_sido(sido: str) -> str:
    """
    시도(광역자치단체) 단위로 치매안심센터 목록을 조회한다.

    사용자가 "서울에 치매안심센터 몇 개야?", "경기도 센터 알려줘"처럼
    시/도 단위로만 지역을 언급했을 때 호출한다. 시군구까지 특정했다면
    get_centers_by_sigungu를 대신 사용한다. "OO센터 홈페이지/주소/전화번호
    알려줘"처럼 특정 지역 센터의 홈페이지를 묻는 질문도 이 함수로 처리한다
    (flexible_graph_search로 보내지 않는다).

    Args:
        sido: 시도명 (예: "서울특별시", "경기도"). "서울", "경기"처럼 줄여
            쓴 경우 정식 명칭으로 보정해서 넣는다.

    Returns:
        센터명/주소/전화번호/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (c:치매안심센터)-[:LOCATED_IN]->(:시군구)-[:LOCATED_IN]->(sd:시도 {name: $sido})
        RETURN c.name AS name, c.주소 AS 주소, c.전화번호 AS 전화번호, c.홈페이지 AS 홈페이지
        LIMIT $limit
        """,
        sido=sido,
    )
    return _format_centers(rows, truncated)


@tool
def get_centers_by_sigungu(sido: str, sigungu: str) -> str:
    """
    시도+시군구(기초자치단체) 단위로 치매안심센터 목록을 조회한다.

    사용자가 "강남구 치매안심센터 알려줘", "수원시 센터 어디야?",
    "OO센터 홈페이지 알려줘"처럼 구/시/군 단위까지 지역을 특정했을 때
    호출한다. 홈페이지를 묻는 질문도 이 함수로 처리한다
    (flexible_graph_search로 보내지 않는다).

    Args:
        sido: 시도명 (예: "서울특별시")
        sigungu: 시군구명 (예: "강남구"). 시군구명이 "중구", "서구"처럼
            여러 시도에 중복되는 이름일 수 있으므로 sido와 함께 넘긴다.

    Returns:
        센터명/주소/전화번호/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (c:치매안심센터)-[:LOCATED_IN]->(sg:시군구 {시도: $sido, name: $sigungu})
        RETURN c.name AS name, c.주소 AS 주소, c.전화번호 AS 전화번호, c.홈페이지 AS 홈페이지
        LIMIT $limit
        """,
        sido=sido,
        sigungu=sigungu,
    )
    return _format_centers(rows, truncated)


@tool
def get_sido_list() -> str:
    """
    전국 시도(광역자치단체) 목록 전체를 조회한다.

    사용자가 "어느 지역까지 지원돼?", "전체 시도 목록 보여줘"처럼
    커버 범위 자체를 물었을 때 호출한다.

    Returns:
        시도명을 쉼표로 구분한 문자열
    """
    rows = _run_query("MATCH (sd:시도) RETURN sd.name AS name ORDER BY sd.name")
    if not rows:
        return "시도 정보를 찾지 못했습니다."
    return ", ".join(r["name"] for r in rows)


@tool
def get_sigungu_list(sido: str) -> str:
    """
    특정 시도 산하의 시군구(기초자치단체) 목록을 조회한다.

    사용자가 "서울에 어떤 구가 있어?"처럼 특정 시도 안의 하위 지역
    목록을 물었을 때 호출한다.

    Args:
        sido: 시도명 (예: "서울특별시")

    Returns:
        시군구명을 쉼표로 구분한 문자열
    """
    rows = _run_query(
        "MATCH (sd:시도 {name: $sido})-[:CONTAINS]->(sg:시군구) RETURN sg.name AS name ORDER BY sg.name",
        sido=sido,
    )
    if not rows:
        return f"{sido}의 시군구 정보를 찾지 못했습니다."
    return ", ".join(r["name"] for r in rows)


# ==============================================================
# 2. 센터명 기준 조회
# ==============================================================
@tool
def search_center_by_name(keyword: str) -> str:
    """
    센터명에 특정 키워드가 포함된 치매안심센터를 검색한다.

    사용자가 정확한 지역이 아니라 센터 이름 일부(예: "강남구치매안심센터",
    "일산서구")로 검색하고 싶을 때 호출한다. "OO센터 홈페이지 알려줘"처럼
    센터명이 포함된 홈페이지 질문도 이 함수로 처리한다.

    Args:
        keyword: 센터명에 포함될 검색어 (예: "강남구", "광역치매센터")

    Returns:
        센터명/주소/전화번호/유형/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (c:치매안심센터)
        WHERE c.name CONTAINS $keyword
        RETURN c.name AS name, c.주소 AS 주소, c.전화번호 AS 전화번호, c.유형 AS 유형, c.홈페이지 AS 홈페이지
        LIMIT $limit
        """,
        keyword=keyword,
    )
    if not rows:
        return "조건에 맞는 치매안심센터를 찾지 못했습니다."
    blocks = []
    for i, r in enumerate(rows, start=1):
        line = f"[{i}] {r['name']} ({r.get('유형', '')}) | 주소: {r.get('주소', '')} | 전화번호: {r.get('전화번호', '')}"
        if r.get("홈페이지"):
            line += f" | 홈페이지: {r['홈페이지']}"
        blocks.append(line)
    text = "\n".join(blocks)
    if truncated:
        text += f"\n\n(조건에 맞는 센터가 {RESULT_LIMIT_DEFAULT}개보다 많아 상위 {RESULT_LIMIT_DEFAULT}개만 표시했습니다. 검색어를 더 구체적으로 좁혀서 다시 물어보시면 전체를 확인하실 수 있습니다.)"
    return text


# ==============================================================
# 3. 운영기관 기준 조회 (신규)
# ==============================================================
@tool
def get_operator_by_center(center_name: str) -> str:
    """
    특정 치매안심센터를 관리(운영)하는 운영기관 정보를 조회한다.

    사용자가 "이 센터는 어디서 운영해?", "OO센터 운영기관 연락처 알려줘",
    "운영기관 홈페이지 알려줘"처럼 센터를 관리하는 상급 기관(보건소/병원 등)의
    정보나 홈페이지를 물었을 때 호출한다.

    Args:
        center_name: 정확한 센터명 (예: "서울특별시강남구치매안심센터")

    Returns:
        운영기관명/대표자명/전화번호/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows = _run_query(
        """
        MATCH (c:치매안심센터 {name: $center_name})-[:MANAGES]-(o:운영기관)
        RETURN o.name AS name, o.대표자명 AS 대표자명, o.전화번호 AS 전화번호, o.홈페이지 AS 홈페이지
        """,
        center_name=center_name,
    )
    if not rows:
        return f"'{center_name}'의 운영기관 정보를 찾지 못했습니다."
    r = rows[0]
    text = f"{r['name']} (대표자: {r.get('대표자명', '정보없음')}, 전화번호: {r.get('전화번호', '정보없음')})"
    if r.get("홈페이지"):
        text += f" | 홈페이지: {r['홈페이지']}"
    return text


@tool
def get_centers_by_operator(operator_name: str) -> str:
    """
    특정 운영기관이 관리하는 치매안심센터 목록을 조회한다.

    사용자가 "OO보건소가 관리하는 센터는?", "OO병원에서 운영하는
    치매안심센터 어디야?"처럼 운영기관을 기준으로 센터를 찾을 때 호출한다.
    이 센터들의 홈페이지를 묻는 질문도 함께 처리한다.
    저장된 관계 방향은 (:운영기관)-[:MANAGES]->(:치매안심센터)이지만, 이
    함수는 반대 방향(운영기관 -> 소속 센터들)으로 조회한다.

    Args:
        operator_name: 정확한 운영기관명 (예: "삼성서울병원", "강남구보건소")

    Returns:
        센터명/주소/전화번호/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (o:운영기관 {name: $operator_name})-[:MANAGES]->(c:치매안심센터)
        RETURN c.name AS name, c.주소 AS 주소, c.전화번호 AS 전화번호, c.홈페이지 AS 홈페이지
        LIMIT $limit
        """,
        operator_name=operator_name,
    )
    if not rows:
        return f"'{operator_name}'이(가) 관리하는 센터 정보를 찾지 못했습니다. 운영기관명이 정확한지 확인해 주세요."
    return _format_centers(rows, truncated)


# ==============================================================
# 4. 프로그램 기준 조회 (신규)
# ==============================================================
@tool
def get_programs_by_center(center_name: str) -> str:
    """
    특정 치매안심센터에서 제공하는 프로그램 목록을 조회한다.

    사용자가 "OO센터에서 하는 프로그램 뭐 있어?", "이 센터 인지훈련
    프로그램 있어?", "프로그램 안내 링크 줘"처럼 센터가 제공하는 서비스나
    그 신청 링크를 물었을 때 호출한다. 프로그램 자체엔 URL이 없으므로
    센터 홈페이지를 대신 안내한다 (flexible_graph_search로 보내지 않는다).

    Args:
        center_name: 정확한 센터명 (예: "서울특별시강남구치매안심센터")

    Returns:
        프로그램명을 쉼표로 구분한 문자열 + (있다면) 센터 홈페이지 안내.
        프로그램 자체는 별도 URL이 없으므로, 프로그램별 링크 대신 그 프로그램을
        제공하는 센터의 홈페이지를 한 번만 안내한다 (LLM이 프로그램마다 없는
        링크를 지어내는 것을 막기 위함). 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (c:치매안심센터 {name: $center_name})-[:PROVIDES]->(p:프로그램)
        RETURN p.name AS name, c.홈페이지 AS 센터홈페이지
        LIMIT $limit
        """,
        center_name=center_name,
    )
    if not rows:
        return f"'{center_name}'의 프로그램 정보를 찾지 못했습니다."
    text = ", ".join(r["name"] for r in rows)
    center_website = rows[0].get("센터홈페이지")
    if center_website:
        text += f"\n\n(각 프로그램의 상세 일정·신청 방법은 프로그램 자체 페이지가 아니라 센터 홈페이지에서 확인하세요: {center_website})"
    if truncated:
        text += f" (그 외에도 더 있어 상위 {RESULT_LIMIT_DEFAULT}개만 표시했습니다.)"
    return text


@tool
def get_centers_by_program(program_keyword: str) -> str:
    """
    특정 프로그램(또는 프로그램명 일부)을 제공하는 치매안심센터 목록을 조회한다.

    사용자가 "치매조기검진 하는 센터 어디야?", "인지훈련 프로그램 있는
    곳 알려줘"처럼 프로그램을 기준으로 센터를 찾을 때 호출한다. 이 센터들의
    홈페이지를 함께 묻는 질문도 처리한다. 저장된
    관계 방향은 (:치매안심센터)-[:PROVIDES]->(:프로그램)이지만, 이 함수는
    반대 방향(프로그램 -> 제공 센터들)으로 조회한다.

    Args:
        program_keyword: 프로그램명 또는 그 일부 (예: "치매조기검진", "인지훈련")

    Returns:
        센터명/주소/전화번호/홈페이지가 포함된 텍스트. 없으면 안내 문구.
    """
    rows, truncated = _run_query_capped(
        """
        MATCH (c:치매안심센터)-[:PROVIDES]-(p:프로그램)
        WHERE p.name CONTAINS $program_keyword
        RETURN DISTINCT c.name AS name, c.주소 AS 주소, c.전화번호 AS 전화번호, c.홈페이지 AS 홈페이지
        LIMIT $limit
        """,
        program_keyword=program_keyword,
    )
    if not rows:
        return f"'{program_keyword}' 프로그램을 제공하는 센터를 찾지 못했습니다."
    return _format_centers(rows, truncated)


# ==============================================================
# 5. fallback (예외/복합 질문용, 최후의 수단)
# ==============================================================
@lru_cache(maxsize=1)
def _get_fallback_chain() -> GraphCypherQAChain:
    """flexible_graph_search 전용 GraphCypherQAChain을 1회만 생성해 재사용한다.

    cypher_prompt.py와 동일한 방향 규칙(관계 5종 정방향 + 역방향 질문 예시)을
    그대로 사용해, 이 fallback 경로에서도 방향 오류가 재발하지 않도록 한다.
    추가로 인원수(int), 경도/위도(float), 우편번호(zero-pad string) 등 필드 타입을
    LLM이 잘못 판단해 조건절이 조용히 실패하는 것을 막기 위해 프로퍼티 타입 규칙을
    프롬프트에 명시한다 ("의사가 2명 이상인 센터" 같은 통계성 질문 대응용).

    Returns:
        GraphCypherQAChain 인스턴스
    """
    graph = Neo4jGraph(
        url=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
    )
    llm = ChatOpenAI(model=FALLBACK_MODEL)

    cypher_template = """당신은 Neo4j Cypher 쿼리 전문가입니다.
아래 스키마를 참고하여 질문에 맞는 Cypher 쿼리를 생성하세요.

스키마:
{schema}

관계 방향 규칙 (반드시 지켜야 함, 저장된 방향은 아래가 전부이며 역방향 관계는 별도로 존재하지 않음):
- (:치매안심센터)-[:LOCATED_IN]->(:시군구)
- (:시도)-[:CONTAINS]->(:시군구)
- (:시군구)-[:LOCATED_IN]->(:시도)
- (:운영기관)-[:MANAGES]->(:치매안심센터)
- (:치매안심센터)-[:PROVIDES]->(:프로그램)

역방향처럼 보이는 질문 처리 규칙:
질문이 저장된 방향과 반대로 묻더라도, 새로운 관계를 만들려 하지 말고
화살표를 생략한 패턴(-[:REL]-)으로 기존 관계를 그대로 타고 가서 조회하세요.

프로퍼티 타입 규칙 (반드시 지켜야 함, {schema}가 float/int를 정확히 구분해주지
못하는 경우가 있으므로 아래를 우선한다):
- (:치매안심센터)의 인원 관련 필드는 정수(int)로 저장되어 있다:
  의사인원수, 간호사인원수, 사회복지사인원수, 인원_작업치료사, 인원_운동치료사,
  인원_임상심리사, 인원_음악치료사, 인원_행정인력, 인원_송영인력, 인원_기타
  → 조건절에 쓸 때는 따옴표로 감싸지 말고 숫자 그대로 비교한다.
    예: WHERE c.의사인원수 >= 2  (WHERE c.의사인원수 >= "2" 아님)
  → 값이 0인 경우와 값이 없는(NULL) 경우는 다르다. 0은 "해당 인력이 배정되어
    있지 않음"을 뜻하는 정상 값이고, NULL은 "데이터 자체가 없음"을 뜻한다.
    "의사가 없는 센터"처럼 물으면 c.의사인원수 = 0을 찾아야 하며, 이를
    IS NULL과 혼동하지 않는다.
- (:치매안심센터)의 우편번호는 문자열(string)로 저장되어 있다 (앞자리 0이 유효한 값,
  예: "06153"). 따옴표로 감싸서 비교한다. 숫자로 비교하지 않는다.
- (:치매안심센터)의 경도, 위도는 float으로 저장되어 있다 (소수점이 위치 정밀도
  자체이므로 정수로 반올림하지 않는다).
- (:치매안심센터)의 전화번호, 팩스번호, 홈페이지, 주소, 유형, name과
  (:운영기관)의 홈페이지, 대표자명, 전화번호와
  (:시도, :시군구, :운영기관, :프로그램)의 name은 전부 문자열(string)이다.
  → 이 필드들은 반드시 따옴표로 감싸서 비교한다.

링크 규칙 (반드시 지켜야 함):
- 프로그램(:프로그램) 노드는 홈페이지/URL 프로퍼티가 없다. 프로그램 관련
  답변에서 링크가 필요하면 그 프로그램을 제공하는 센터의 홈페이지를
  사용하고, URL이 없는 항목을 절대 링크로 만들지 않는다.

질문: {question}
Cypher 쿼리:"""

    cypher_prompt = PromptTemplate(input_variables=["schema", "question"], template=cypher_template)

    return GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        allow_dangerous_requests=True,
        cypher_prompt=cypher_prompt,
        top_k=RESULT_LIMIT_DEFAULT,
        # 알려진 한계: GraphCypherQAChain은 내부적으로 context를 top_k로
        # 슬라이싱만 하고 "잘렸는지 여부"를 밖으로 알려주지 않는다. 앞의 9개
        # 고정 tool과 달리 이 fallback은 결과가 top_k를 넘게 잘려도 그 사실을
        # 답변에 명시하지 못한다. 개선하려면 GraphCypherQAChain을 상속해
        # context 길이를 별도로 노출하는 커스텀 체인을 만들어야 한다.
    )


@tool
def flexible_graph_search(query: str) -> str:
    """
    위 9개 tool로 답하기 어려운 복잡하거나 예외적인 그래프 질문에 사용한다.

    지역/센터명/운영기관/프로그램 기준의 단순 조회는 반드시 해당 전용
    tool(get_centers_by_sido, get_centers_by_sigungu, search_center_by_name,
    get_operator_by_center, get_centers_by_operator, get_programs_by_center,
    get_centers_by_program 등)을 먼저 사용하고, 이 tool은 그것들로 커버되지
    않는 경우에만 최후의 수단으로 사용한다.

    예: "프로그램을 가장 많이 제공하는 센터는?", "간호사와 임상심리사가
    모두 있는 센터는?", "시도별 센터 개수를 많은 순으로 알려줘"처럼 집계/
    복합 조건이 섞인 질문.

    이 tool은 내부적으로 LLM이 질문마다 새 Cypher를 즉석 생성하므로, 위 9개
    tool보다 방향 오류 등의 위험이 있다. 9개로 답이 되는 질문에는 쓰지 않는다.

    주의 (링크 관련): (:프로그램) 노드는 홈페이지/URL 프로퍼티가 없다.
    "OO 프로그램 안내 링크 줘" 같은 질문이라도 이 tool로 오면 안 되고,
    get_programs_by_center 또는 get_centers_by_program을 사용해야 한다.
    이 두 tool은 프로그램 자체에 없는 링크 대신, 그 프로그램을 제공하는
    센터의 실제 홈페이지를 안내하도록 이미 구현되어 있다.

    Args:
        query: 사용자의 원본 질문(자연어)

    Returns:
        생성된 Cypher 실행 결과를 바탕으로 한 자연어 답변
    """
    chain = _get_fallback_chain()
    response = chain.invoke({"query": query})
    return response["result"]


# ==============================================================
# 단독 실행 테스트
# ==============================================================
if __name__ == "__main__":
    print("== get_centers_by_sido ==")
    print(get_centers_by_sido.invoke({"sido": "세종특별자치시"}))
    print("-" * 50)

    print("== get_centers_by_sigungu ==")
    print(get_centers_by_sigungu.invoke({"sido": "서울특별시", "sigungu": "강남구"}))
    print("-" * 50)

    print("== get_sido_list ==")
    print(get_sido_list.invoke({}))
    print("-" * 50)

    print("== get_sigungu_list ==")
    print(get_sigungu_list.invoke({"sido": "세종특별자치시"}))
    print("-" * 50)

    print("== search_center_by_name ==")
    print(search_center_by_name.invoke({"keyword": "강남구"}))
    print("-" * 50)

    print("== get_operator_by_center ==")
    print(get_operator_by_center.invoke({"center_name": "서울특별시강남구치매안심센터"}))
    print("-" * 50)

    print("== get_centers_by_operator ==")
    print(get_centers_by_operator.invoke({"operator_name": "삼성서울병원"}))
    print("-" * 50)

    print("== get_programs_by_center ==")
    print(get_programs_by_center.invoke({"center_name": "서울특별시강남구치매안심센터"}))
    print("-" * 50)

    print("== get_centers_by_program ==")
    print(get_centers_by_program.invoke({"program_keyword": "치매조기검진"}))
    print("-" * 50)

    print("== flexible_graph_search ==")
    print(flexible_graph_search.invoke({"query": "프로그램을 가장 많이 제공하는 센터는 어디야?"}))
    print("-" * 50)
