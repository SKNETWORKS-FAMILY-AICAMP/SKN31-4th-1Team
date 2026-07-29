# -*- coding: utf-8 -*-
"""
GraphCypherQAChain용 Cypher 생성 프롬프트

지난 1차 적재 때 LLM이 (:치매안심센터)-[:LOCATED_IN]->(:시군구) 방향을 반대로
생성해서 "서울에 센터 0개"라는 오답이 났던 문제가 있었다. extra_instructions로는
방향이 안 잡혀서 cypher_prompt를 직접 작성해 방향을 못 박아야 했다.

이번엔 관계가 5종(LOCATED_IN, CONTAINS, MANAGES, PROVIDES)으로 늘었으므로,
전부 정방향을 명시하고, "역방향처럼 보이는 질문"(예: 프로그램 기준으로 센터
찾기, 운영기관 기준으로 관리 센터 찾기)에 대한 올바른 Cypher 예시까지
프롬프트에 포함한다. 저장은 단방향으로만 하고, 역방향 조회는 화살표를 생략한
MATCH 패턴(-[:REL]-)으로 처리하도록 안내한다.

사용 방법:
    from cypher_prompt import CYPHER_PROMPT
    graph_chain = GraphCypherQAChain.from_llm(
        llm=llm, graph=graph, allow_dangerous_requests=True,
        verbose=True, cypher_prompt=CYPHER_PROMPT,
    )
"""
from langchain_core.prompts import PromptTemplate


CYPHER_GENERATION_TEMPLATE = """당신은 Neo4j Cypher 쿼리 전문가입니다.
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
질문이 "이 프로그램 하는 센터는?", "이 보건소가 관리하는 센터는?"처럼
저장된 방향과 반대 방향으로 묻더라도, 새로운 관계를 만들려 하지 말고
화살표를 생략한 패턴(-[:REL]-)으로 기존 관계를 그대로 타고 가서 조회하세요.

올바른 예시 1 (정방향 - 지역 기준 센터 조회):
MATCH (c:치매안심센터)-[:LOCATED_IN]->(sg:시군구)-[:LOCATED_IN]->(sd:시도 {{name: '서울특별시'}})
WHERE sg.name = '강남구'
RETURN c.name, c.주소, c.전화번호

올바른 예시 2 (정방향 - 센터가 제공하는 프로그램 조회):
MATCH (c:치매안심센터 {{name: '서울특별시강남구치매안심센터'}})-[:PROVIDES]->(p:프로그램)
RETURN p.name, p.category

올바른 예시 3 (역방향 질문 - 프로그램 기준으로 센터 찾기):
MATCH (c:치매안심센터)-[:PROVIDES]-(p:프로그램 {{name: '치매조기검진'}})
RETURN c.name, c.주소, c.전화번호

올바른 예시 4 (역방향 질문 - 운영기관 기준으로 관리 센터 찾기):
MATCH (o:운영기관 {{name: '강남구보건소'}})-[:MANAGES]-(c:치매안심센터)
RETURN c.name, c.주소

올바른 예시 5 (역방향 질문 - 센터가 소속된 운영기관 찾기):
MATCH (c:치매안심센터 {{name: '서울특별시강남구치매안심센터'}})-[:MANAGES]-(o:운영기관)
RETURN o.name, o.대표자명, o.전화번호

질문: {question}
Cypher 쿼리:"""

CYPHER_PROMPT = PromptTemplate(
    input_variables=["schema", "question"],
    template=CYPHER_GENERATION_TEMPLATE,
)
