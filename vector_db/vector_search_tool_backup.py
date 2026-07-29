"""
증상/가이드라인 정보 검색 툴 (Agent Tool)

에이전트가 "증상 정보가 필요하다"고 판단했을 때 호출하는 툴.
Qdrant(dementia_guideline 컬렉션)에서 질의와 의미적으로 유사한 청크를 검색해
LLM이 답변 생성 시 참고할 수 있는 형태(텍스트 + 출처)로 반환한다.

LangChain의 @tool 데코레이터를 사용해 LangGraph 에이전트(ToolNode 등)에
그대로 bind 할 수 있도록 구성.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.tools import tool
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "dementia_guideline"
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"

TOP_K_DEFAULT = 4
SCORE_THRESHOLD = 0.4  # 코사인 유사도 기준, 너무 낮은 관련도 결과는 제외


# ------------------------------------------------------------
# 클라이언트/모델은 프로세스당 한 번만 로드 (lru_cache로 재사용)
# ------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_client() -> QdrantClient:
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise EnvironmentError(
            "QDRANT_URL / QDRANT_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요."
        )
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


@lru_cache(maxsize=1)
def _get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def _format_results(hits) -> str:
    """검색 결과를 LLM이 읽기 좋은 텍스트 블록으로 정리"""
    if not hits:
        return "관련된 검진 가이드라인 정보를 찾지 못했습니다."

    blocks = []
    for i, hit in enumerate(hits, start=1):
        payload = hit.payload
        blocks.append(
            f"[참고자료 {i}] (출처: {payload.get('title', '알 수 없음')}, "
            f"관련도: {hit.score:.2f})\n"
            f"{payload.get('text', '')}\n"
            f"URL: {payload.get('source_url', '')}"
        )
    return "\n\n".join(blocks)


@tool
def search_dementia_guideline(query: str, top_k: int = TOP_K_DEFAULT) -> str:
    """
    치매 조기 증상 및 검진 가이드라인 정보를 벡터 검색으로 조회한다.

    사용자가 기억력 저하, 언어 표현 어려움, 반복 질문, 방향감각 상실 등
    치매 의심 증상이나 검진 절차, 검진 대상 기준, 비용 지원 등을 질문했을 때 호출한다.

    Args:
        query: 검색할 자연어 질의 (예: "최근에 같은 말을 자꾸 반복해요")
        top_k: 반환할 관련 문서 개수 (기본 4개)

    Returns:
        관련 가이드라인 텍스트와 출처가 포함된 문자열
    """
    client = _get_client()
    model = _get_embedding_model()

    query_vector = model.encode([query], normalize_embeddings=True)[0].tolist()

    result = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
        score_threshold=SCORE_THRESHOLD,
        with_payload=True,

    )

    hits = result.points

    return _format_results(hits)


if __name__ == "__main__":
    # 단독 실행 테스트
    test_queries = [
        "부모님이 자꾸 같은 질문을 반복하세요",
        "치매 조기검진 비용은 얼마인가요",
        "길을 잃어버리는 것도 치매 증상인가요",
    ]
    for q in test_queries:
        print(f"\n질의: {q}")
        print(search_dementia_guideline.invoke({"query": q}))
        print("-" * 50)
