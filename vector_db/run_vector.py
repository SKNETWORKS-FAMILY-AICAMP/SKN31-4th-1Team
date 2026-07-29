"""
전체 파이프라인 통합 실행
: 수집 -> 정제 -> chunking -> Qdrant 적재

실행:
    python run_all.py

각 단계는 함수로 분리되어 있어 특정 단계만 다시 돌리고 싶으면
main() 안에서 필요한 함수만 주석 처리하고 실행하면 됩니다.
"""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib import robotparser

from dotenv import load_dotenv

import requests
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from openai import OpenAI

load_dotenv()

# ------------------------------------------------------------
# 공통 경로 설정 (현재 디렉토리 기준)
# ------------------------------------------------------------
RAW_DIR = Path("data") / "raw"
PROCESSED_DIR = Path("data") / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

RAW_GUIDELINE_PATH = RAW_DIR / "guideline_raw.json"
CLEANED_PATH = PROCESSED_DIR / "guideline_cleaned.json"
CHUNKS_PATH = PROCESSED_DIR / "guideline_chunks.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Educational-Project-Crawler; contact: your_email@example.com)"
}

TARGET_URLS = [
    "https://ansim.nid.or.kr/introduce/early_service.aspx", # 치매안심센터 치매조기검진사업
    "https://www.mohw.go.kr/menu.es?mid=a10712010100", # 보건복지부 치매조기검진사업
    "https://www.easylaw.go.kr/CSP/OnhunqueansInfoRetrieve.laf?onhunqnaAstSeq=97&onhunqueSeq=4461", # 법제처 찾기쉬운 생활법령 정보
    "https://www.mentalhealth.go.kr/portal/disease/diseaseDetail.do?dissId=22", # 국가정신건강정보포털 | 정신건강정보 > 질환별 정보
    "https://www.nid.or.kr/info/diction_list5.aspx?gubun=0506", # 중앙치매센터
    "https://clinic.paju.go.kr/clinic/clinic_03/clinic_03_14/clinic_03_14_02.jsp", # 파주시 보건소
]

CHUNK_SIZE = 400
CHUNK_OVERLAP = 50

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
COLLECTION_NAME = "dementia_guideline"
EMBEDDING_MODEL_NAME = "text-embedding-3-small"  # OpenAI API 기반 (로컬 메모리 사용 없음)
VECTOR_SIZE = 1536  # text-embedding-3-small 차원
RECREATE_COLLECTION = True  # ⚠️ 기존에 랜덤 UUID로 중복 적재된 데이터 정리 위해 1회 True로 실행 후, 다시 False로 되돌릴 것

NAV_PATTERNS = [
    r"홈\s*>\s*.*?>.*",
    r"^(이전|다음|목록|인쇄|공유|top|TOP)$",
    r"^\s*Copyright.*",
    r"^\s*All rights reserved.*",
]


# ==============================================================
# STEP 1. 수집 (collect_guideline.py)
# ==============================================================
def is_allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(HEADERS["User-Agent"], url)
    except Exception:
        return False


def extract_text_from_page(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    main_content = (
        soup.select_one("div.sub_cont")
        or soup.select_one("div#contents_body")
        or soup.select_one("div#print-content")
        or soup.select_one("div#contents > ul.question")
        # or soup.select_one("div.contDescription")
        # or soup.select_one("div.reset_css_form")
        or soup.select_one("div.board_con")
        or soup.select_one("article#content")
        or soup.select_one("div.cntWrap.dictionWrap")
        or soup.select_one("div.content-body")
    )
    text = main_content.get_text(separator="\n", strip=True) if main_content else ""

    return {
        "url": url,
        "title": soup.title.string.strip() if soup.title else "",
        "text": text,
    }


def step1_collect():
    print("\n===== STEP 1. 가이드라인 원문 수집 =====")
    collected = []
    for url in TARGET_URLS:
        if not is_allowed_by_robots(url):
            print(f"[SKIP] robots.txt 차단: {url}")
            continue
        try:
            page_data = extract_text_from_page(url)
            collected.append(page_data)
            print(f"[OK] {url} ({len(page_data['text'])}자)")
        except Exception as e:
            print(f"[FAIL] {url} - {e}")
        time.sleep(1)

    with open(RAW_GUIDELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {RAW_GUIDELINE_PATH} (총 {len(collected)}개 문서)")


# ==============================================================
# STEP 2. 정제 (clean_text.py)
# ==============================================================
def clean_line(line: str):
    line = line.strip()
    if len(line) < 5:
        return None
    for pattern in NAV_PATTERNS:
        if re.match(pattern, line, flags=re.IGNORECASE):
            return None
    return re.sub(r"\s+", " ", line)


def clean_document(doc: dict) -> dict:
    lines = doc.get("text", "").split("\n")
    cleaned_lines, seen = [], set()
    for line in lines:
        cl = clean_line(line)
        if cl and cl not in seen:
            cleaned_lines.append(cl)
            seen.add(cl)
    cleaned_text = "\n".join(cleaned_lines)
    return {
        "url": doc.get("url"),
        "title": doc.get("title", "").strip(),
        "text": cleaned_text,
        "char_count": len(cleaned_text),
    }


def step2_clean():
    print("\n===== STEP 2. 문서 정제 =====")
    with open(RAW_GUIDELINE_PATH, encoding="utf-8") as f:
        raw_docs = json.load(f)

    cleaned_docs = [clean_document(d) for d in raw_docs]
    cleaned_docs = [d for d in cleaned_docs if d["char_count"] > 30]

    with open(CLEANED_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned_docs, f, ensure_ascii=False, indent=2)

    for d in cleaned_docs:
        print(f"[정제완료] {d['title']} - {d['char_count']}자")
    print(f"저장 완료: {CLEANED_PATH} ({len(cleaned_docs)}개 문서)")


# ==============================================================
# STEP 3. Chunking (chunking.py)
# ==============================================================
def build_chunks(doc: dict, splitter: RecursiveCharacterTextSplitter) -> list:
    chunks = splitter.split_text(doc["text"])
    result = []
    for i, chunk_text in enumerate(chunks):
        # source_url + chunk_index + text 기반 결정론적 id
        # -> 동일 내용 재실행 시 같은 id가 생성되어 Qdrant upsert 시 덮어쓰기됨 (중복 방지)
        hash_input = f"{doc['url']}|{i}|{chunk_text}".encode("utf-8")
        deterministic_id = str(uuid.UUID(hashlib.md5(hash_input).hexdigest()))

        result.append({
            "chunk_id": deterministic_id,
            "text": chunk_text,
            "metadata": {
                "title": doc["title"],
                "source_url": doc["url"],
                "source_type": "guideline",
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
        })
    return result


def step3_chunk():
    print("\n===== STEP 3. Chunking =====")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )

    with open(CLEANED_PATH, encoding="utf-8") as f:
        docs = json.load(f)

    all_chunks = []
    for doc in docs:
        doc_chunks = build_chunks(doc, splitter)
        all_chunks.extend(doc_chunks)
        print(f"[chunking] {doc['title']} -> {len(doc_chunks)}개 청크")

    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {CHUNKS_PATH} (총 {len(all_chunks)}개 청크)")


# ==============================================================
# STEP 4. 임베딩 + Qdrant 적재 (load_qdrant.py)
# ==============================================================
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection(client: QdrantClient, recreate: bool = False):
    if recreate and client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
        print(f"기존 컬렉션 삭제: {COLLECTION_NAME}")

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"컬렉션 생성 완료: {COLLECTION_NAME}")
    else:
        print(f"기존 컬렉션 사용: {COLLECTION_NAME}")


def get_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    return OpenAI(api_key=OPENAI_API_KEY)


def embed_and_upload(client: QdrantClient, openai_client: OpenAI, chunks: list, batch_size: int = 100):
    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start:start + batch_size]
        texts = [c["text"] for c in batch]

        # OpenAI 임베딩 API 호출 (로컬 모델 로드/메모리 사용 없음)
        response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL_NAME,
            input=texts,
        )
        vectors = [item.embedding for item in response.data]

        points = [
            PointStruct(
                id=chunk["chunk_id"],
                vector=vector,
                payload={"text": chunk["text"], **chunk["metadata"]}
            )
            for chunk, vector in zip(batch, vectors)
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        print(f"업로드 진행: {min(start + batch_size, total)}/{total}")


def step4_load_qdrant():
    print("\n===== STEP 4. 임베딩 + Qdrant 적재 =====")
    if not QDRANT_URL or not QDRANT_API_KEY:
        raise EnvironmentError(
            "QDRANT_URL / QDRANT_API_KEY가 설정되어 있지 않습니다. "
            "프로젝트 루트에 .env 파일을 만들고 아래 형식으로 값을 채워주세요.\n"
            "QDRANT_URL=https://xxxx.aws.cloud.qdrant.io\n"
            "QDRANT_API_KEY=your_api_key"
        )

    print("OpenAI 임베딩 클라이언트 준비 중...")
    openai_client = get_openai_client()

    client = get_qdrant_client()
    ensure_collection(client, recreate=RECREATE_COLLECTION)

    with open(CHUNKS_PATH, encoding="utf-8") as f:
        chunks = json.load(f)

    print(f"총 {len(chunks)}개 청크 임베딩 및 적재 시작")
    embed_and_upload(client, openai_client, chunks)

    info = client.get_collection(COLLECTION_NAME)
    print(f"적재 완료. points_count: {info.points_count}")


# ==============================================================
# 전체 실행
# ==============================================================
def main():
    step1_collect()
    step2_clean()
    step3_chunk()
    step4_load_qdrant()
    print("\n✅ 전체 파이프라인 완료")


if __name__ == "__main__":
    main()