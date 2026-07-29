"""프로젝트 전역 설정 상수 모듈 by 진영

graph_db/, vector_db/, modules/(LangGraph 파이프라인)에서 공통으로 쓰는
모델명, 경로, DB 접속 정보 등을 모아둔다.

============================================================
[팀원 온보딩 가이드]
============================================================

1. 가상환경 세팅
   uv venv .venv --python 3.12.9
   .venv\\Scripts\\activate   (Windows)
   uv pip install -r requirements.txt

2. .env 파일 획득 방법
   디스코드 '환경설정' 채널에 공유된 구글드라이브 링크에서 .env 파일을 다운로드하여
   이 config.py와 같은 폴더(프로젝트 루트)에 넣는다.
   (구글드라이브는 팀원 Gmail 계정만 접근 가능하도록 공유 권한이 제한되어 있음.
    절대 이 파일이나 Git에 값을 직접 커밋하지 말 것.)

   .env에는 아래 키들이 포함되어 있어야 한다:

       LLM_MODEL=gpt-5.4-mini
       FALLBACK_LLM_MODEL=              (비워두면 LLM_MODEL과 동일하게 사용)

       NEO4J_URI=neo4j+s://19f5f7c5.databases.neo4j.io
       NEO4J_USERNAME=neo4j
       NEO4J_PASSWORD=<발급받은 비밀번호>
       NEO4J_DATABASE=neo4j

       QDRANT_URL=https://<cluster-id>.<region>.aws.cloud.qdrant.io
       QDRANT_API_KEY=<발급받은 키>
       OPENAI_API_KEY=<발급받은 키>            (VectorDB 임베딩용, text-embedding-3-small)

       SUPABASE_URL=<Supabase 프로젝트 URL>
       SUPABASE_KEY=<Supabase API 키>

3. DB 접속 방법
   - Qdrant: https://cloud.qdrant.io 로그인 → 클러스터 선택 → Dashboard에서
     URL / API Key 확인 (API Key는 최초 생성 시 1회만 노출되므로 분실 시 재발급)
   - Neo4j: https://console.neo4j.io 접속 → Gmail로 온 초대메일에서
     Accept 클릭 → 인스턴스 접속

4. 가이드라인: 각자 담당 폴더의 md 파일을 읽고 시작할 것
5. AI 코딩 도구(Claude Code) 사용 시: CLAUDE.md를 먼저 읽힌 뒤 코딩 시작

============================================================
[전역 상수 정의]
============================================================
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- 경로 설정 ---
BASE_DIR = Path(__file__).resolve().parent  # 프로젝트 root 경로
# Path(__file__).resolve().parent: config.py가 있는 폴더(=프로젝트 루트)

GRAPH_DB_DIR = BASE_DIR / "graph_db"
GRAPH_DATA_RAW_DIR = GRAPH_DB_DIR / "data" / "raw"
GRAPH_DATA_PROCESSED_DIR = GRAPH_DB_DIR / "data" / "processed"

VECTOR_DB_DIR = BASE_DIR / "vector_db"


# --- 모델 설정 ---
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.4-mini")
# flexible_graph_search(그래프 fallback tool) 전용 LLM. 기본은 LLM_MODEL과 동일하게 두되,
# 필요하면 .env에서 FALLBACK_LLM_MODEL만 따로 override 할 수 있다.
FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", LLM_MODEL)


# --- Neo4j (GraphDB) 설정 ---
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE")
# --- GraphDB 조회 결과 상한 ---
RESULT_LIMIT_DEFAULT = 200


# --- Qdrant (VectorDB) 설정 ---
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

COLLECTION_NAME = "dementia_guideline"
EMBEDDING_MODEL_NAME = "text-embedding-3-small"
EMBEDDING_DIMS = 1536  # text-embedding-3-small 기본 출력 차원. bge-m3(1024)에서 변경됨.

TOP_K_DEFAULT = 4
SCORE_THRESHOLD = 0.3  # OpenAI 임베딩은 bge-m3와 유사도 분포가 달라 threshold를 낮춰서 시작 (실제 검색 결과 보며 조정 권장)


# --- Supabase (사용자 인증 / 대화 컨텍스트 보관) 설정 ---
# --- 장단기 메모리 Database 파일 경로 (=> Supabase-PostSql)
# 아직 실제 인증 로직은 구현 전. .env에 값만 채워두면 이후 auth 모듈에서 바로 가져다 쓸 수 있다.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
