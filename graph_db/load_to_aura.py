# -*- coding: utf-8 -*-
"""
치매알람 GraphDB 적재 스크립트

data/processed/ 에 있는 노드 CSV 5종, 관계 CSV 5종을 읽어서
Neo4j AuraDB에 MERGE 방식으로 적재한다.

- MERGE를 사용하므로 스크립트를 여러 번 실행해도 노드/관계가 중복 생성되지 않는다.
- 실행 순서: (1) uniqueness constraint 생성 -> (2) 노드 5종 적재 -> (3) 관계 5종 적재
  노드가 먼저 있어야 관계를 연결할 수 있으므로 이 순서를 반드시 지켜야 한다.
- 환경변수(.env)에서 NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD / NEO4J_DATABASE를 읽는다.

실행 방법:
    cd graph_db
    python3 load_to_aura.py
"""
import os
import math
import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, 'data', 'processed')

NEO4J_URI = os.getenv('NEO4J_URI')
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE')


def load_csv(filename):
    """data/processed 폴더에서 CSV를 읽어 DataFrame으로 반환한다.

    Args:
        filename (str): data/processed 폴더 기준 파일명 (예: 'nodes_시도.csv')

    Returns:
        pandas.DataFrame: 읽어들인 데이터프레임
    """
    path = os.path.join(PROCESSED_DIR, filename)
    return pd.read_csv(path, encoding='utf-8-sig')


def clean_props(row_dict):
    """DataFrame row(dict)에서 NaN 값을 제거해 Neo4j에 넣을 프로퍼티 dict를 만든다.

    NaN을 그대로 넣으면 프로퍼티가 null로 명시적으로 세팅되어 스키마가 지저분해지므로,
    값이 없는 프로퍼티는 아예 dict에서 제외한다.

    Args:
        row_dict (dict): pandas row.to_dict() 결과

    Returns:
        dict: NaN이 제거된 프로퍼티 dict
    """
    cleaned = {}
    for k, v in row_dict.items():
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        cleaned[k] = v
    return cleaned


def create_constraints(session):
    """5종 노드에 대한 uniqueness constraint를 생성한다.

    MERGE 시 중복 방지 및 조회 성능 확보를 위해 각 노드의 식별 프로퍼티에
    유니크 제약조건을 건다. 이미 존재하면 IF NOT EXISTS로 스킵된다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    constraints = [
        "CREATE CONSTRAINT sido_name IF NOT EXISTS FOR (n:시도) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT sigungu_key IF NOT EXISTS FOR (n:시군구) REQUIRE (n.시도, n.name) IS UNIQUE",
        "CREATE CONSTRAINT center_id IF NOT EXISTS FOR (n:치매안심센터) REQUIRE n.센터ID IS UNIQUE",
        "CREATE CONSTRAINT operator_id IF NOT EXISTS FOR (n:운영기관) REQUIRE n.운영기관ID IS UNIQUE",
        "CREATE CONSTRAINT program_id IF NOT EXISTS FOR (n:프로그램) REQUIRE n.프로그램ID IS UNIQUE",
    ]
    for c in constraints:
        session.run(c)
    print('제약조건 생성 완료 (5종)')


def load_sido(session):
    """:시도 노드를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('nodes_시도.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MERGE (n:시도 {name: row.name})
        """,
        rows=rows,
    )
    print(f':시도 적재 완료: {len(rows)}개')


def load_sigungu(session):
    """:시군구 노드를 적재한다. (시도, name) 조합으로 유니크하게 식별한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('nodes_시군구.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MERGE (n:시군구 {시도: row.시도, name: row.name})
        """,
        rows=rows,
    )
    print(f':시군구 적재 완료: {len(rows)}개')


def load_center(session):
    """:치매안심센터 노드를 적재한다.

    직종별 인원수(인원_* 컬럼)를 포함해 모든 프로퍼티를 그대로 넣되,
    NaN 값은 clean_props()로 제거한 뒤 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('nodes_치매안심센터.csv')
    rows = [clean_props(r) for r in df.to_dict('records')]
    session.run(
        """
        UNWIND $rows AS row
        MERGE (n:치매안심센터 {센터ID: row.센터ID})
        SET n += row
        """,
        rows=rows,
    )
    print(f':치매안심센터 적재 완료: {len(rows)}개')


def load_operator(session):
    """:운영기관 노드를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('nodes_운영기관.csv')
    rows = [clean_props(r) for r in df.to_dict('records')]
    session.run(
        """
        UNWIND $rows AS row
        MERGE (n:운영기관 {운영기관ID: row.운영기관ID})
        SET n += row
        """,
        rows=rows,
    )
    print(f':운영기관 적재 완료: {len(rows)}개')


def load_program(session):
    """:프로그램 노드를 적재한다.

    category는 아직 표준 카테고리 분류 전이라 비어있을 수 있다 (null 허용).

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('nodes_프로그램.csv')
    rows = [clean_props(r) for r in df.to_dict('records')]
    session.run(
        """
        UNWIND $rows AS row
        MERGE (n:프로그램 {프로그램ID: row.프로그램ID})
        SET n += row
        """,
        rows=rows,
    )
    print(f':프로그램 적재 완료: {len(rows)}개')


def load_rel_sigungu_located_in_sido(session):
    """(:시군구)-[:LOCATED_IN]->(:시도) 관계를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('rels_시군구_LOCATED_IN_시도.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MATCH (sg:시군구 {시도: row.시도, name: row.시군구})
        MATCH (sd:시도 {name: row.시도})
        MERGE (sg)-[:LOCATED_IN]->(sd)
        """,
        rows=rows,
    )
    print(f'(:시군구)-[:LOCATED_IN]->(:시도) 적재 완료: {len(rows)}개')


def load_rel_sido_contains_sigungu(session):
    """(:시도)-[:CONTAINS]->(:시군구) 관계를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('rels_시도_CONTAINS_시군구.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MATCH (sd:시도 {name: row.시도})
        MATCH (sg:시군구 {시도: row.시도, name: row.시군구})
        MERGE (sd)-[:CONTAINS]->(sg)
        """,
        rows=rows,
    )
    print(f'(:시도)-[:CONTAINS]->(:시군구) 적재 완료: {len(rows)}개')


def load_rel_center_located_in_sigungu(session):
    """(:치매안심센터)-[:LOCATED_IN]->(:시군구) 관계를 적재한다.

    센터 CSV에는 시도 컬럼이 없으므로, 센터ID로 센터 노드를 먼저 찾고
    그 노드의 시도 프로퍼티를 이용해 시군구를 매칭한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('rels_센터_LOCATED_IN_시군구.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MATCH (c:치매안심센터 {센터ID: row.센터ID})
        MATCH (sg:시군구 {시도: c.시도, name: row.시군구})
        MERGE (c)-[:LOCATED_IN]->(sg)
        """,
        rows=rows,
    )
    print(f'(:치매안심센터)-[:LOCATED_IN]->(:시군구) 적재 완료: {len(rows)}개')


def load_rel_operator_manages_center(session):
    """(:운영기관)-[:MANAGES]->(:치매안심센터) 관계를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('rels_운영기관_MANAGES_센터.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MATCH (o:운영기관 {운영기관ID: row.운영기관ID})
        MATCH (c:치매안심센터 {센터ID: row.센터ID})
        MERGE (o)-[:MANAGES]->(c)
        """,
        rows=rows,
    )
    print(f'(:운영기관)-[:MANAGES]->(:치매안심센터) 적재 완료: {len(rows)}개')


def load_rel_center_provides_program(session):
    """(:치매안심센터)-[:PROVIDES]->(:프로그램) 관계를 적재한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    df = load_csv('rels_센터_PROVIDES_프로그램.csv')
    rows = df.to_dict('records')
    session.run(
        """
        UNWIND $rows AS row
        MATCH (c:치매안심센터 {센터ID: row.센터ID})
        MATCH (p:프로그램 {프로그램ID: row.프로그램ID})
        MERGE (c)-[:PROVIDES]->(p)
        """,
        rows=rows,
    )
    print(f'(:치매안심센터)-[:PROVIDES]->(:프로그램) 적재 완료: {len(rows)}개')


def verify(session):
    """적재 결과를 검증한다. 노드/관계 라벨별 개수를 세서 출력한다.

    Args:
        session (neo4j.Session): Neo4j 세션 객체
    """
    print('\n=== 적재 검증 ===')
    node_labels = ['시도', '시군구', '치매안심센터', '운영기관', '프로그램']
    for label in node_labels:
        result = session.run(f'MATCH (n:{label}) RETURN count(n) AS cnt')
        print(f':{label} 노드 개수: {result.single()["cnt"]}')

    rel_types = ['LOCATED_IN', 'CONTAINS', 'MANAGES', 'PROVIDES']
    for rel in rel_types:
        result = session.run(f'MATCH ()-[r:{rel}]->() RETURN count(r) AS cnt')
        print(f'[:{rel}] 관계 개수: {result.single()["cnt"]}')

    # 샘플 조회: 서울 강남구 치매안심센터가 제공하는 프로그램
    sample = session.run(
        """
        MATCH (c:치매안심센터)-[:PROVIDES]->(p:프로그램)
        WHERE c.시도 = '서울특별시' AND c.시군구 = '강남구'
        RETURN c.name AS center, collect(p.name) AS programs
        """
    )
    for record in sample:
        print(f"\n샘플 조회 - {record['center']}: {record['programs'][:5]} 등 {len(record['programs'])}개")


def main():
    """전체 적재 파이프라인을 실행한다.

    실행 순서: 제약조건 생성 -> 노드 5종 적재 -> 관계 5종 적재 -> 검증
    """
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    with driver.session(database=NEO4J_DATABASE) as session:
        create_constraints(session)

        load_sido(session)
        load_sigungu(session)
        load_center(session)
        load_operator(session)
        load_program(session)

        load_rel_sigungu_located_in_sido(session)
        load_rel_sido_contains_sigungu(session)
        load_rel_center_located_in_sigungu(session)
        load_rel_operator_manages_center(session)
        load_rel_center_provides_program(session)

        verify(session)
    driver.close()
    print('\n적재 완료.')


if __name__ == '__main__':
    main()
