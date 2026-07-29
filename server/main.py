import sys
import os

# 부모 디렉토리(SKN31-3rd-1Team)를 시스템 경로에 추가하여 
# 어디서 실행하든 server.* 와 vector_db.* 모듈을 찾을 수 있게 합니다.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from typing import List, Dict
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent

from server.agent import build_agent
from server.context_loader import load_context, save_and_summarize
import json


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
    user_id: str
    user_name: str
    messages: List[Dict[str, str]]

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
def chat_endpoint(request: ChatRequest, background_tasks: BackgroundTasks):
    # 1. 프론트엔드에서 받은 메시지 중 가장 마지막 사용자의 질문만 추출
    last_user_message = ""
    for msg in reversed(request.messages):
        if msg.get("role") == "user":
            last_user_message = msg.get("content", "")
            break
            
    # 2. Supabase에서 이전 대화 맥락(요약 + 최근 N턴) 가져오기
    chat_id, summary, recent = load_context(request.user_id)
    
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
        config={"configurable": {"user_id": request.user_id}}
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
        
    background_tasks.add_task(save_and_summarize, request.user_id, chat_id, last_user_message, ai_text_to_save)

    return {
        "session_id": request.user_id,
        "response": response_data
    }