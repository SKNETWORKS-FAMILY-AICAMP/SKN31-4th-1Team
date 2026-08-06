import sys
import os

# 부모 디렉토리(SKN31-3rd-1Team)를 시스템 경로에 추가하여 
# 어디서 실행하든 server.* 와 vector_db.* 모듈을 찾을 수 있게 합니다.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from typing import List, Dict, Optional
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent

from server.agent import build_agent
from server.context_loader import load_context, save_and_summarize
import json
from server.auth import verify_token
from server.daily_summary import summarize_checkin
from server.daily_checkin import (
    get_today_checkin,
    save_checkin,
    decide_next_turn,
    DuplicateCheckinError,
)

app = FastAPI(
    title="치매 안내 챗봇 API",
    description="치매 안내 관련 LLM 챗봇 서비스를 위한 백엔드 API",
    version="1.0.0",
)

# CORS 설정
origins = [
    "http://localhost:5173",                 # 로컬 개발 환경 (Vite 기본 포트)
    "http://127.0.0.1:5173",                 # 로컬 개발 환경 (127.0.0.1)
    "https://dementia-front.vercel.app",     # Vercel 배포 환경
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]

class CheckinRequest(BaseModel):
    messages: List[Dict[str, str]]
    is_finishing: bool = False  # "대화 마치기" 버튼을 직접 눌렀는지 (decide_next_turn에 전달)

is_first_health_check = True

def trigger_deploy_webhook():
    global is_first_health_check
    if is_first_health_check:
        is_first_health_check = False
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if webhook_url:
            try:
                import requests
                requests.post(webhook_url, json={"content": "🚀 **치매 안내 챗봇 API** 배포가 완료되어 서버가 정상적으로 시작되었습니다!"})
            except Exception as e:
                print(f"Discord webhook failed: {e}")

@app.get("/")
def read_root():
    trigger_deploy_webhook()
    return {"message": "치매 안내 챗봇 API 서버가 정상적으로 실행 중입니다."}

@app.get("/health")
def health_check():
    trigger_deploy_webhook()
    return {"status": "ok"}


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest, background_tasks: BackgroundTasks, user=Depends(verify_token)):
    user_id = user["sub"]
    # 1. 프론트엔드에서 받은 메시지 중 가장 마지막 사용자의 질문만 추출
    last_user_message = ""
    for msg in reversed(request.messages):
        if msg.get("role") == "user":
            last_user_message = msg.get("content", "")
            break
            
    # 2. Supabase에서 이전 대화 맥락(요약 + 최근 N턴) 가져오기
    chat_id, summary, recent = load_context(user_id)
    
    formatted_messages = []
    if summary:
        formatted_messages.append(("system", f"[이전 대화 요약]\n{summary}"))
        
    for turn in recent:
        if "user" in turn:
            formatted_messages.append(("user", turn["user"]))
        if "ai" in turn:
            formatted_messages.append(("assistant", turn["ai"]))
            
    # 최신 사용자 질문 추가
    formatted_messages.append(("user", last_user_message))

    # 3. 캐싱된 에이전트 인스턴스 가져오기
    agent = build_agent()

    # 3. 에이전트 실행 (단건 질문만 전달)
    result = agent.invoke(
        {
            "messages": formatted_messages,
            "structured_response": None,
            "final_response": None
        },
        config={"configurable": {"user_id": user_id}}
    )

    # 5. 구조화된 응답 추출 (LangGraph 최종 출력은 final_response)
    structured = result["final_response"]
    response_data = structured.model_dump()
    
    # 6. 백그라운드 태스크로 DB에 새 대화 저장 및 롤링 요약 실행 (응답 지연 없음)
    # response_data 전체(JSON)를 저장하면 토큰이 기하급수적으로 폭발하므로, 
    # AI가 사용자에게 실제로 한 말(text 또는 question)만 추출해서 맥락으로 저장합니다.
    if response_data["type"] == "reply":
        ai_text_to_save = response_data["content"]["text"]
    else:
        ai_text_to_save = response_data["content"]["question"]
        
    background_tasks.add_task(save_and_summarize, user_id, chat_id, last_user_message, ai_text_to_save)

    return {
        "session_id": user_id,
        "response": response_data
    }

# --- 회원 탈퇴 엔드포인트 ---
@app.delete("/api/delete-account")
def delete_account(user=Depends(verify_token)):
    user_id = user["sub"]
    try:
        from supabase import create_client, Client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_service_key = os.getenv("SUPABASE_SERVICE_KEY")
        
        if not supabase_url or not supabase_service_key:
            print("Delete Account Error: Supabase Service Key is not configured on the server.")
            raise HTTPException(status_code=500, detail="회원 탈퇴 처리 중 오류가 발생했습니다.")
            
        # Admin 클라이언트 생성 (service_role)
        supabase_admin: Client = create_client(supabase_url, supabase_service_key)
        
        # 유저 삭제 전에 아바타 이미지 정리 (avatars 버킷 내 유저 폴더)
        try:
            # avatars 버킷의 사용자 폴더 내 파일 목록 조회
            files = supabase_admin.storage.from_("avatars").list(user_id)
            if files:
                file_paths = [f"{user_id}/{f['name']}" for f in files]
                # 파일 일괄 삭제
                supabase_admin.storage.from_("avatars").remove(file_paths)
        except Exception as e:
            # 파일 삭제 실패가 유저 삭제 실패로 이어지지 않게 처리
            print(f"Delete Avatars Error (Ignored): {e}")

        # 유저 삭제 실행
        supabase_admin.auth.admin.delete_user(user_id)
        return {"status": "success", "message": "계정이 성공적으로 탈퇴 처리되었습니다."}
    except HTTPException:
        # 이미 우리가 던진 HTTPException은 그대로 통과
        raise
    except Exception as e:
        print(f"Delete Account Error: {e}")
        raise HTTPException(status_code=500, detail="회원 탈퇴 처리 중 오류가 발생했습니다.")

# --- 오늘의 대화 (데일리 체크인) ---
# 프론트엔드 계약: mds/daily_checkin_widget_guide.md 3절
# (dementia_front 저장소에 이미 구현·배포됨. 이 계약대로 응답해야 한다)

@app.get("/api/checkin/today")
def checkin_today(user=Depends(verify_token)):
    """오늘(KST) 이미 체크인했는지 조회. 프론트가 예방 탭 진입 시 1회 호출한다."""
    user_id = user["sub"]
    checkin = get_today_checkin(user_id)
    if checkin:
        return {"checked_in": True, "checkin": checkin}
    return {"checked_in": False, "checkin": None}


@app.post("/api/checkin")
def checkin_turn(request: CheckinRequest, user=Depends(verify_token)):
    """
    대화 턴 처리 + 완료 시 저장을 겸한다.
    - 대화를 더 이어가야 하면: {"type": "turn", "reply": "..."}
    - 마무리할 시점이면: 요약을 생성해 daily_checkins에 저장하고
      {"type": "complete", "summary", "tone", "concern_note", "observations"}
    """
    user_id = user["sub"]
    messages = request.messages

    decision = decide_next_turn(messages, force_finish=request.is_finishing)

    if decision["action"] == "continue":
        return {"type": "turn", "reply": decision["reply"]}

    # action == "finish": 구조화된 요약 생성 (tone 검증은 summarize_checkin 내부에서 처리)
    result = summarize_checkin(messages)
    user_turn_count = sum(1 for m in messages if m.get("role") == "user")

    try:
        save_checkin(user_id, result, user_turn_count)
    except DuplicateCheckinError:
        # 다른 탭/기기에서 이미 오늘 체크인을 완료한 경우.
        # 프론트는 이 응답을 받으면 최신 상태를 재조회해 정상 완료 흐름으로 처리한다.
        raise HTTPException(status_code=409, detail="이미 오늘의 체크인을 완료했습니다.")
    except Exception as e:
        print(f"Save Checkin Error: {e}")
        raise HTTPException(status_code=500, detail="체크인 저장 중 오류가 발생했습니다.")

    return {
        "type": "complete",
        "summary": result["summary"],
        "tone": result["tone"],
        "concern_note": result["concern_note"],
        "observations": result["observations"],
    }


# --- Keepalive 엔드포인트: DB활성화 ---
@app.get("/keepalive")
def keepalive():
    from graph_db.graph_search_tool import _run_query
    from qdrant_client import QdrantClient

    # Neo4j CUD (더미 노드 생성 후 즉시 삭제)
    _run_query("CREATE (k:_Keepalive {ts: datetime()}) WITH k DELETE k")

    # Qdrant read
    client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"])
    info = client.get_collection("dementia_guideline")

    return {"neo4j": "ok", "qdrant": info.points_count}