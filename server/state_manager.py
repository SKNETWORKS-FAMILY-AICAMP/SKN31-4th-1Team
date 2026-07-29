import os
from supabase import create_client, Client
from dotenv import load_dotenv
from typing import Dict, Any
from datetime import datetime, timezone

load_dotenv()

# ==========================================
# 1. 화이트리스트 상수 선언
# ==========================================
VALID_SYMPTOMS = {
    "기억력저하", "반복질문", "시간장소혼동", "언어장애", "물건분실", "판단력저하",
    "성격변화", "흥미상실", "일상수행곤란", "배회", "망상환각", "수면장애", "배설곤란"
}

VALID_SAFETY_FLAGS = {
    "급격한변화", "실종위험", "화기위험", "운전위험", "자타해위험", "낙상반복"
}

# ==========================================
# 2. 상태 병합 로직 (비즈니스 룰)
# ==========================================
def merge_state(current_state: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    LLM이 제안한 패치(patch)를 기존 상태(current_state)에 안전하게 병합합니다.
    (상태_업데이트_규칙.md 반영)
    """
    # 1) 기본 데이터 구조 보장
    state = {
        "symptoms": current_state.get("symptoms") or [],
        "safety_flags": current_state.get("safety_flags") or [],
        "duration": current_state.get("duration"),
        "progression": current_state.get("progression"),
        "adl_impact": current_state.get("adl_impact"),
        "notes": current_state.get("notes") or []
    }

    if not patch:
        return state

    # 2) 목록형 필드 (합집합 + 화이트리스트 검사) - 삭제 불가
    patch_symptoms = patch.get("symptoms") or []
    for s in patch_symptoms:
        if s in VALID_SYMPTOMS and s not in state["symptoms"]:
            state["symptoms"].append(s)

    patch_safety = patch.get("safety_flags") or []
    for f in patch_safety:
        if f in VALID_SAFETY_FLAGS and f not in state["safety_flags"]:
            state["safety_flags"].append(f)

    # 3) 단일값 필드 (최신값 갱신 및 정정 이력 남기기)
    single_value_keys = ["duration", "progression", "adl_impact"]
    for key in single_value_keys:
        new_val = patch.get(key)
        # null(None) 값으로 기존 값을 덮어쓰지 않음
        if new_val is not None:
            old_val = state.get(key)
            if old_val is not None and old_val != new_val:
                state["notes"].append(f"{key} 정정: {old_val} -> {new_val}")
            state[key] = new_val

    # 4) 단순 메모 추가
    note = patch.get("note")
    if note:
        state["notes"].append(note)

    return state

# ==========================================
# 3. 대화 단계(Phase) 동적 판정 로직
# ==========================================
def calculate_phase(state_data: Dict[str, Any]) -> str:
    """
    현재 상태를 기반으로 대화 단계(Phase)를 도출합니다.
    - ESCALATE: 긴급 (안전신호 발생)
    - READY: 정보 수집 완료 (증상 + 기간 + 일상지장여부 확인)
    - COLLECTING: 정보 수집 중
    """
    if state_data.get("safety_flags"):
        return "ESCALATE"
        
    if (state_data.get("symptoms") and 
        state_data.get("duration") and 
        state_data.get("adl_impact") is not None):
        return "READY"
        
    return "COLLECTING"

# ==========================================
# 4. Supabase 연동 클래스
# ==========================================
class StateManager:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if url and key:
            self.client: Client = create_client(url, key)
        else:
            self.client = None
            print("Warning: Supabase credentials not found for StateManager.")

    def get_state(self, subject_id: str) -> Dict[str, Any]:
        """DB에서 대상자(subject_id)의 현재 상태를 불러옵니다."""
        if not self.client:
            return {}

        try:
            res = self.client.table("state").select("data").eq("subject_id", subject_id).execute()
            if res.data and len(res.data) > 0:
                return res.data[0].get("data") or {}
        except Exception as e:
            print(f"Error fetching state: {e}")
        return {}

    def update_state(self, subject_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        패치를 받아 병합을 수행하고 DB에 Upsert 한 뒤, phase가 추가된 결과물을 반환합니다.
        """
        if not self.client:
            return {}

        # 1. 불러오기
        current_state = self.get_state(subject_id)

        # 2. 병합
        new_state = merge_state(current_state, patch)

        # 3. DB 갱신 (upsert)
        try:
            self.client.table("state").upsert({
                "subject_id": subject_id,
                "data": new_state,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            print(f"Error updating state: {e}")

        # 4. Phase 계산 (DB 저장 안 함, 서버 메모리 반환용)
        final_result = new_state.copy()
        final_result["phase"] = calculate_phase(new_state)
        
        return final_result

# 단독 테스트
if __name__ == "__main__":
    current = {
        "symptoms": ["반복질문"],
        "duration": "6개월 이상",
        "adl_impact": None
    }
    
    # 예시 패치: 기간 변경(정정), 허용되지 않은 증상코드(우울함) 포함, adl_impact 추가
    patch = {
        "symptoms": ["배회", "우울함"], # 우울함은 무시되어야 함
        "duration": "1년쯤", # 정정 이력 발생
        "adl_impact": True,
        "note": "보호자가 매우 지쳐보임",
        "user_id": "hack_attempt" # 무시되어야 함
    }
    
    print("=== Before ===")
    print(current)
    
    merged = merge_state(current, patch)
    
    print("\n=== After Merge ===")
    print(merged)
    
    print(f"\n=== Phase ===")
    print(calculate_phase(merged))
