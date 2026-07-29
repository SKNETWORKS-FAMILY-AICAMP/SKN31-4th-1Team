import os
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

# Supabase 초기화
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") # 백엔드에서는 Service Key 사용

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

# 요약용 가벼운 LLM
summary_llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")

def is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

def load_context(user_id: str):
    """
    유저의 가장 최근 대화 세션(contexts)을 가져오거나 새로 생성합니다.
    return: (chat_id, summary_str, recent_list)
    """
    if not supabase:
        print("Warning: Supabase client is not initialized.")
        return None, "", []
        
    # 1. 비로그인 유저 예외 처리 (UUID가 아니면 패스)
    if not is_valid_uuid(user_id):
        return None, "", []

    try:
        # 2. 유저의 가장 최근 세션 조회
        response = supabase.table("contexts").select("*").eq("user_id", user_id).order("updated_at", desc=True).limit(1).execute()
        data = response.data
        
        if data and len(data) > 0:
            row = data[0]
            chat_id = row.get("chat_id")
            conversation = row.get("conversation") or {}
            summary = conversation.get("summary", "")
            recent = conversation.get("recent", [])
            return chat_id, summary, recent
        else:
            # 3. 없으면 새 세션 생성
            new_chat_id = str(uuid.uuid4())
            new_row = {
                "chat_id": new_chat_id,
                "user_id": user_id,
                "conversation": {"summary": "", "recent": []}
            }
            supabase.table("contexts").insert(new_row).execute()
            return new_chat_id, "", []
            
    except Exception as e:
        print(f"Error in load_context: {e}")
        return None, "", []

def summarize_old_message(oldest_turn: dict, existing_summary: str) -> str:
    """
    기존 요약본과 가장 오래된 1턴을 결합하여 새로운 요약본을 생성합니다.
    """
    sys_prompt = "당신은 치매 상담 챗봇의 대화 맥락을 요약하는 어시스턴트입니다. 이전 요약본과 추가된 대화를 바탕으로 핵심 정보(환자 상태, 증상 등)를 훼손하지 않으면서 간결하게 하나의 요약본으로 병합하세요."
    
    user_msg = f"""
[이전 요약본]
{existing_summary if existing_summary else "없음"}

[추가할 과거 대화 1턴]
보호자: {oldest_turn.get('user', '')}
상담봇: {oldest_turn.get('ai', '')}

이 내용들을 바탕으로 전체 대화의 흐름과 핵심 정보를 하나의 문단으로 요약해주세요.
"""
    try:
        res = summary_llm.invoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_msg)
        ])
        content = res.content
        if isinstance(content, list):
            content = " ".join([c.get("text", "") for c in content if isinstance(c, dict) and "text" in c])
        return content.strip() if isinstance(content, str) else str(content)
    except Exception as e:
        print(f"Error in summarize_old_message: {e}")
        return existing_summary

def save_and_summarize(user_id: str, chat_id: str, user_text: str, ai_text: str):
    """
    새로운 대화 턴을 DB에 저장하고, 5턴을 초과하면 롤링 요약(Rolling Summary)을 수행합니다.
    """
    if not supabase or not chat_id:
        return
        
    try:
        # 현재 상태 다시 불러오기
        response = supabase.table("contexts").select("conversation").eq("chat_id", chat_id).execute()
        if not response.data:
            return
            
        conversation = response.data[0].get("conversation") or {"summary": "", "recent": []}
        summary = conversation.get("summary", "")
        recent = conversation.get("recent", [])
        
        # 새 대화 추가
        recent.append({"user": user_text, "ai": ai_text})
        
        # 5턴 초과 시 롤링 요약
        if len(recent) > 5:
            oldest_turn = recent.pop(0)
            summary = summarize_old_message(oldest_turn, summary)
            
        # DB 업데이트
        updated_conversation = {
            "summary": summary,
            "recent": recent
        }
        # updated_at 갱신을 위해 내장 기능에 맡기거나 명시적 update 처리 (Supabase는 보통 trigger로 처리됨)
        supabase.table("contexts").update({"conversation": updated_conversation}).eq("chat_id", chat_id).execute()
        
    except Exception as e:
        print(f"Error in save_and_summarize: {e}")
