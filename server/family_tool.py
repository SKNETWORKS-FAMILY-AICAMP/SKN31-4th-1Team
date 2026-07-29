import os
from typing import Dict, Any
from supabase import create_client, Client
from langchain_core.tools import tool
from langchain_core.runnables.config import RunnableConfig

class FamilyInfoTool:
    """사용자가 등록한 가족(대상자) 정보를 Supabase에서 조회하는 도우미 클래스"""
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if url and key:
            self.client: Client = create_client(url, key)
        else:
            self.client = None
            print("Warning: Supabase credentials not found for FamilyInfoTool.")

    def get_family_info(self, user_id: str) -> str:
        if not self.client:
            return "Supabase 연동 오류: 정보를 조회할 수 없습니다."
        
        try:
            # 1. subjects 테이블에서 user_id가 등록한 대상자 목록 조회
            res = self.client.table("subjects").select("*").eq("user_id", user_id).execute()
            if not res.data:
                return "현재 등록된 가족(환자) 정보가 없습니다."
            
            result_lines = ["[등록된 가족 목록]"]
            for subject in res.data:
                subject_id = subject.get("subject_id")
                relation = subject.get("relation", "알 수 없음")
                birth_year = subject.get("birth_year", "알 수 없음")
                region = subject.get("region", "알 수 없음")
                
                info_str = f"- {relation} (출생연도: {birth_year}, 거주지역: {region}, 대상자ID: {subject_id})"
                
                # 2. state 테이블에서 해당 대상자의 파악된 상태 같이 조회
                state_res = self.client.table("state").select("data").eq("subject_id", subject_id).execute()
                if state_res.data and len(state_res.data) > 0:
                    state_data = state_res.data[0].get("data", {})
                    symptoms = state_data.get("symptoms", [])
                    duration = state_data.get("duration", "")
                    
                    if symptoms or duration:
                        info_str += f"\n  └ 파악된 상태: 증상({', '.join(symptoms) if symptoms else '없음'}), 지속기간({duration or '모름'})"
                
                result_lines.append(info_str)
            
            return "\n".join(result_lines)
            
        except Exception as e:
            return f"가족 정보 조회 중 오류가 발생했습니다: {str(e)}"


# ------------------------------------------------------------------
# LLM에게 제공될 실제 툴 노드
# ------------------------------------------------------------------
@tool
def query_family_info(config: RunnableConfig) -> str:
    """
    현재 사용자가 등록해둔 치매 의심 환자(가족)의 기본 정보(관계, 출생연도, 지역)와 
    기존에 파악된 증상 정보를 데이터베이스에서 조회합니다.
    환자의 정보나 대상자ID(subject_id)가 필요할 때 가장 먼저 호출하세요.
    """
    # 에이전트 invoke 시 넘겨준 configurable 에서 user_id를 꺼냄
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "오류: 현재 접속한 사용자의 ID(user_id)를 확인할 수 없어 가족 정보를 조회할 수 없습니다."
        
    tool_instance = FamilyInfoTool()
    return tool_instance.get_family_info(user_id)
