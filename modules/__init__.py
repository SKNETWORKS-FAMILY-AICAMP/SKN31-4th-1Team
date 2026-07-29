"""
modules

개별 LLM 호출을 담당하는 모듈 모음.
(pipeline/ 이 이 모듈들을 가져다 LangGraph 노드로 엮는다)

- modules.extractor : LLM ① 추출기 — 발화에서 정보를 JSON으로 뽑는다 (효민)
- modules.responder  : LLM ② 응답기 — 질문 + DB 조회 결과로 답변을 만든다 (연아)

각 모듈은 파일 하나로 되어 있다 (schema/prompt/node를 나누지 않음).
프로젝트 규모가 크지 않고 각자 한 파일씩 담당하고 있어서,
여러 파일로 쪼개는 것보다 한 파일 안에서 위->아래로 읽히는 편이 낫다고 판단했다.
"""
