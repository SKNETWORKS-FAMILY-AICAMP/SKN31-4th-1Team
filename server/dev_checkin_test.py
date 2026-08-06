# dev_checkin_test.py — 로컬 실험용 (서버/토큰 없이 직접 DB 저장)
# 실험 전용! 실제 저장 창구는 리드가 토큰 기반으로 만듦. 이 파일은 프로덕션 아님.
# 이 파일과 .env(서버 키)는 절대 깃/프론트에 올리지 말 것.
import os, json
from datetime import date
from supabase import create_client
from dotenv import load_dotenv

from server.daily_summary_backup import summarize_checkin   # 내가 만든 함수

load_dotenv()

# 본인이 이 서비스에 로그인할 때 쓰는 이메일 (구글 로그인이면 그 구글 이메일)
MY_EMAIL = "dusdk9549@gmail.com"

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY"),   # 서버 전용 키 (RLS 우회 -> 저장 가능)
)


def get_my_user_id(email: str) -> str:
    """서버 키로 auth 유저 목록에서 내 이메일의 UUID를 찾음."""
    users = supabase.auth.admin.list_users()
    for u in users:
        if u.email == email:
            return u.id
    raise ValueError(f"{email} 유저를 못 찾았어요. 이메일을 확인하세요.")


def run(messages: list[dict]):
    my_id = get_my_user_id(MY_EMAIL)
    print(f"내 user_id: {my_id}")

    # 1. 내가 만든 함수로 대화 -> 요약 JSON
    result = summarize_checkin(messages)
    print("\nAI 요약 결과:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 2. tone 값 확인 (4개 중 하나여야 함)
    assert result["tone"] in ("reassure", "neutral", "observe", "suggest_consult"), \
        f"tone 값이 이상해요: {result['tone']}"

    # 3. 오늘 이미 넣은 게 있으면 지움 (하루 한 줄 제약 때문에, 실험 반복용)
    supabase.table("daily_checkins").delete()\
        .eq("user_id", my_id).eq("checkin_date", str(date.today())).execute()

    # 4. 진짜 저장 (리드가 서버에서 넣는 것과 같은 칸 구성)
    row = {
        "user_id": my_id,
        "checkin_date": str(date.today()),
        "summary": result["summary"],
        "tone": result["tone"],
        "concern_note": result.get("concern_note", ""),
        "observations": result.get("observations", []),
        "turn_count": sum(1 for m in messages if m["role"] == "user"),
    }
    res = supabase.table("daily_checkins").insert(row).execute()
    print("\n저장 완료!")
    print(json.dumps(res.data, ensure_ascii=False, indent=2))
    print("\n-> Supabase 대시보드 > Table Editor > daily_checkins 에서 확인해보세요!")


if __name__ == "__main__":
    # 실험용 가짜 대화 (summarize_checkin이 받는 messages 형식 그대로)
    sample = [
        {"role": "user",      "content": "오늘은 좀 피곤하네. 어제 잠을 설쳤어."},
        {"role": "assistant", "content": "저런, 요즘 자주 그러세요?"},
        {"role": "user",      "content": "한 사나흘 됐나. 자꾸 새벽에 깨."},
        {"role": "assistant", "content": "낮에는 좀 어떠셨어요?"},
        {"role": "user",      "content": "낮엔 괜찮아. 산책도 했고."},
    ]
    run(sample)