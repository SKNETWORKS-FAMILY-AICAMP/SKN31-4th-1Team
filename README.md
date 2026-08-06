# SKN31-3rd-1Team

<br>

# 1. 팀 및 팀원 소개


### 1.1 팀 명
<!-- TODO -->
<center><h3><b> Team | 📋건망검진  </b></h3></center>

---

### 1.2 팀원 및 담당업무
| 유진영 | 박연아 | 김효민 | 김동민 |
| :---: | :---: | :---: | :---: |
| <a href="https://github.com/ujneg18-source"><img src="https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=GitHub&logoColor=white"/></a> | <a href="https://github.com/yeona9549"><img src="https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=GitHub&logoColor=white"/></a> | <a href="https://github.com/hyomin0357"><img src="https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=GitHub&logoColor=white"/></a> | <a href="https://github.com/hyomin0357"><img src="https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=GitHub&logoColor=white"/></a> |
| <img src="산출물/images/슬픔이.png" width="150" height="150"> | <img src="산출물/images/부럽이.png" width="150" height="150"> | <img src="산출물/images/기쁨이.png" width="150" height="150"> | <img src="산출물/images/당황이.png" width="150" height="150"> |
| **GraphDB 설계**<br><sub>데이터 수집 및 전처리</sub><br><sub>Agent Tool 개발</sub> | **백엔드**<br><sub>LangGraph·AI Agent 설계</sub><br><sub>프롬프트 엔지니어링</sub> | **PM · 백엔드**<br><sub>LangGraph·AI Agent 설계</sub><br><sub>프롬프트 엔지니어링</sub> | **프론트엔드**<br><sub>웹UI 구현(챗봇 인터페이스)</sub><br><sub>백엔드 API 연동</sub> |

---

### 1.3 기술 스택 🛠

<p>

<!-- Skill Icons -->
<img src="https://skillicons.dev/icons?i=python,fastapi,react,vite,vercel,supabase" />
<br><br>

<!-- Backend & AI -->
<img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
<img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white"/>
<img src="https://img.shields.io/badge/LangChain-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white"/>
<img src="https://img.shields.io/badge/LangGraph-4B5563?style=for-the-badge&logo=langchain&logoColor=white"/>
<img src="https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white"/>
<img src="https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white"/>
<img src="https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white"/> 
<br>

<!-- Databases -->
<img src="https://img.shields.io/badge/Neo4j_AuraDB-4581C3?style=for-the-badge&logo=neo4j&logoColor=white"/>
<img src="https://img.shields.io/badge/Qdrant_Cloud-DC244C?style=for-the-badge&logo=qdrant&logoColor=white"/>
<img src="https://img.shields.io/badge/Supabase-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white"/>
<br>

<!-- Frontend & Deployment -->
<img src="https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB"/>
<img src="https://img.shields.io/badge/Vite-B73BFE?style=for-the-badge&logo=vite&logoColor=FFD62E"/>
<img src="https://img.shields.io/badge/Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white"/>
<img src="https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render&logoColor=white"/>
<img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black"/> 
<img src="https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white"/> 
<img src="https://img.shields.io/badge/AWS_EC2-FF9900?style=for-the-badge&logo=amazonec2&logoColor=white"/>

</p>

---
### 1.4 시스템 아키텍쳐

<div align="center">
<img src="산출물/images/시스템플로우.png" width="800">
</div>
<br>
<details>
<summary><b>Supabase ERD</b></summary>

## 1.4.1 🏗️ 테이블 관계도 (전체 구조 요약)
- **`auth.users`** (로그인 계정)를 중심으로 3개의 핵심 기능(상담, 체크인, 게임)이 뻗어나갑니다.
- **`posts`** (게시글)는 회원 정보와 무관하게 단독으로 존재합니다.

```text
[auth.users] (회원 가입/로그인 계정)
  ├── 1:N ─▶ [subjects] (등록된 보호 대상 가족) ── 1:1 ─▶ [state] (대상자 건강 상태 요약)
  ├── 1:N ─▶ [contexts] (AI 상담 챗봇 대화 세션 기록)
  ├── 1:N ─▶ [daily_checkins] (오늘의 대화 하루 1회 요약본)
  └── 1:N ─▶ [game_scores] (두뇌 게임 플레이 결과)

[posts] (플랫폼에서 제공하는 예방 정보 게시글)
```
---

## 🗄️ 테이블별 직관적 1줄 요약 명세서

### 1. `subjects` (보호 대상자)
> 💡 사용자가 돌보고 있는 대상 가족(어머니, 아버지 등)의 기본 프로필을 등록해 두는 곳입니다.

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `subject_id` | PK | 이 대상자를 구분하는 고유 번호 |
| `user_id` | FK | 대상자를 등록한 사용자의 로그인 계정 번호 (`auth.users`) |
| `relation` | | 등록자와의 관계 (예: "어머니") |
| `birth_year` | | 대상자가 태어난 연도 (예: 1950) |
| `region` | | 대상자가 현재 살고 있는 지역 (예: "서울 강남구") |
| `created_at` | | 이 대상자를 앱에 처음 등록한 날짜/시간 |
| `updated_at` | | 대상자 정보를 마지막으로 수정한 날짜/시간 |

### 2. `state` (상담 증상 요약본)
> 💡 AI 챗봇 상담을 통해 파악된 대상자의 증상이나 특징들을 요약해서 담아두는 곳입니다 (진단이 아니라 상담 중 파악된 내용을 정리해두는 용도).

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `subject_id` | PK, FK | 상태를 기록할 대상자의 고유 번호 (1명당 딱 1개 행만 가짐) |
| `data` | | "건망증 심함, 수면 부족" 등 AI가 종합한 증상 데이터를 JSON 통째로 저장 |
| `updated_at` | | 이 증상 상태가 마지막으로 업데이트된 날짜/시간 |

### 3. `contexts` (상담 대화 내역)
> 💡 사용자와 AI 챗봇이 나눈 대화 흐름을 잊지 않도록 요약 및 최근 대화 내용을 저장하는 곳입니다.

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `chat_id` | PK | 챗봇 대화 방(세션)을 구분하는 고유 번호 |
| `user_id` | FK | 챗봇과 대화하고 있는 사용자의 로그인 계정 번호 (`auth.users`) |
| `conversation` | | 이전 대화의 전체 요약본과 최근 나눈 대화 몇 마디를 JSON 통째로 저장 |
| `created_at` | | 이 챗봇 대화방이 처음 열린 날짜/시간 |
| `updated_at` | | 가장 최근에 챗봇과 말을 주고받은 날짜/시간 |

### 4. `daily_checkins` (오늘의 대화 결과)
> 💡 매일 가볍게 하루를 묻는 "오늘의 대화" 위젯 결과를 하루 1회만 요약해서 저장하는 곳입니다.

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `id` | PK | 오늘의 대화 기록 고유 번호 (순서대로 증가) |
| `user_id` | FK | 대화에 참여한 사용자의 로그인 계정 번호 (`auth.users`) |
| `checkin_date` | UQ* | 대화를 나눈 날짜 (*단독이 아니라 `user_id`와의 조합으로 유니크 — 같은 사용자는 하루 1개만, 다른 사용자는 같은 날짜에 각자 저장 가능) |
| `summary` | | "오늘은 편안한 하루였다" 등 AI가 한 줄로 요약해준 내용 |
| `tone` | | AI가 대화를 보고 고른 4단계 안내 톤 — 위험도 판정이 아니라 다음 행동을 권유하는 용도 (안심/보통/관찰 권함/상담 권유) |
| `concern_note` | | AI가 왜 그 톤을 골랐는지 근거로 남긴 메모 (안심/보통이면 보통 빈 값) |
| `observations` | | 대화 중에서 발견된 주요 키워드 리스트 (예: ["우울함", "건망증"]) |
| `turn_count` | | 오늘 이 체크인을 위해 AI와 주고받은 대화 횟수 |
| `created_at` | | 이 대화 결과가 서버에 저장된 날짜/시간 |

### 5. `game_scores` (두뇌 게임 기록)
> 💡 사용자가 스도쿠, 카드 뒤집기 등 게임을 한 판 끝낼 때마다 점수와 난이도를 저장하는 곳입니다.

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `id` | PK | 게임 플레이 기록 고유 번호 (순서대로 증가) |
| `user_id` | FK | 게임을 플레이한 사용자의 로그인 계정 번호 (`auth.users`) |
| `game_type` | | 사용자가 플레이한 게임 종류 (예: `puzzle`, `card_match`) |
| `score` | | 게임에서 획득한 최종 점수 또는 소요 시간 |
| `detail` | | 선택한 난이도("쉬움", "보통") 등 게임 관련 상세 부가 옵션 (JSON 저장) |
| `play_date` | | 게임을 플레이한 날짜 (차트 통계 계산용) |
| `played_at` | | 게임을 완료한 정확한 날짜와 시간 |
| `created_at` | | 이 기록이 서버에 저장된 날짜/시간 |

### 6. `posts` (예방 정보 게시글)
> 💡 회원가입 유무와 상관없이 앱 내 모두에게 보여줄 건강 관련 읽을거리(콘텐츠)를 모아둔 곳입니다.

| 컬럼명 | 키 | 직관적 1줄 설명 |
| :--- | :---: | :--- |
| `id` | PK | 게시글을 구분하는 고유 번호 (순서대로 증가) |
| `title` | | 앱 화면에 노출될 게시글 제목 |
| `summary` | | 게시글 목록에서 미리 보여줄 짧은 내용 요약 |
| `category` | | 글이 속한 카테고리 (예: "식습관", "운동") |
| `content` | | 마크다운(MD) 형식으로 작성된 게시글 실제 본문 텍스트 |
| `thumbnail_url` | | 게시글을 대표하는 썸네일(미리보기 이미지) 주소 |
| `read_minutes` | | 이 글을 끝까지 읽는 데 예상되는 소요 시간(분 단위) |
| `source` | | 외부에서 퍼온 글일 경우 표기할 원본 출처 기관 및 링크 |
| `is_featured` | | 예방 탭 맨 위 "오늘의 추천" 캐러셀에 띄울 글인지 여부 (`true`/`false`) |
| `published_at` | | 이 글이 게시된 날짜/시간 (목록 정렬 기준이 되는 값) |
| `created_at` | | 이 게시글 행이 DB에 처음 생성된 날짜/시간 |
| `updated_at` | | 이 게시글을 마지막으로 수정한 날짜/시간 |

</details>

### 1.5 WBS

<details>
<summary>펼치기</summary>

<div align="center">
<img src="산출물/images/WBS.svg" width="1000">
</div>

</details>

---

## 2. 프로젝트 개요

### 2.1 프로젝트명
- AI 치매 정보 알리미
- https://dementia-front.vercel.app/

### 2.2 주제

**3차에서 구축한 "LLM을 연동한 내·외부 문서 기반 질의 응답 시스템"(GraphDB/VectorDB
기반 RAG 챗봇)을 실제 사용 가능한 웹 서비스로 고도화한다.** 3차가 챗봇 코어(에이전트,
GraphDB, VectorDB)를 완성하는 데 집중했다면, 4차는 그 코어를 감싸는 **회원 관리, 예방
콘텐츠, 두뇌 게임, 센터 찾기 지도, 자기관리 저널(오늘의 대화)까지 갖춘 하나의 완결된
서비스**로 확장하는 것이 목표다.

### 2.3 배경 및 선정 이유

<div align="center">
<img src="산출물/images/중앙치매센터_치매인식조사.png" width="550" height="350">
</div>
<br>

- 3차 프로젝트에서 검증한 RAG 챗봇의 핵심 가치(정확한 정보 안내)는 그대로 유지하되,
  실제 보호자가 서비스를 "쓸 이유"를 넓히는 데 집중했다. 상담만 하고 끝나는 게 아니라,
  상담 이후 실제 행동(센터 방문, 예방 활동, 꾸준한 기록)으로 이어지도록 기능을 설계했다.

- 중앙치매센터 조사에서 확인된 문제의식(3차 README 2.3절 참고 — 치매안심센터 **인지도**
  49.4% 대비 **실제 방문 경험률** 12.1%)을 4차에서 한 단계 더 풀었다. "안다"에서
  "가본다"로 이어지는 간극을 좁히기 위해 **치매 센터 찾기** 기능을 새로 만들어,
  상담에서 언급된 지역을 실제 지도 위 가까운 센터로 바로 연결한다.

- 예방(76.2%)·원인/증상(49.9%) 정보 수요가 크다는 점에 착안해, 상담 챗봇 하나에
  머무르지 않고 **예방 콘텐츠 · 두뇌 게임 · 오늘의 대화(자기관리 저널)** 를 더해 사용자가 주기적으로 돌아오는 서비스로 설계했다. 3차는 랜딩 페이지와 AI 상담 챗봇뿐이었고, 이 세 축은 전부 4차에서 처음 만들었다.

<br>               

### 2.4 주요 기능 및 요구사항
| 구분 | 기능 | 설명 |
|------|------|------|
| **메인** | 서비스 랜딩 | 치매 정보 알리미 및 예방 플랫폼 소개 |
| **정보** | 치매 예방 가이드 | 일상생활 수칙, 식습관, 운동, 수면 등 카테고리별 건강 가이드라인 제공 |
| **게시판** | 목록 조회 | 커뮤니티에 작성된 예방 관련 정보 및 후기 리스트 렌더링 |
| **미디어**| 이미지 업로드 | 게시글 내 첨부 이미지 업로드 및 최적화 처리 |
| **게임**| 치매 예방 게임 | 뇌 활성화를 위한 인지 기능 향상 게임 제공 |
| **게임**| 내 게임 기록 차트 | 사용자별 기록을 시각적 차트로 제공하여 인지 변화 추이 관리 |
| **서비스**| 치매센터 찾기 | 사용자 위치 기반/지역 검색을 통한 전국 치매안심센터 위치 및 연락처 정보 안내 |
| **건강**| 오늘의 체크 | 일상의 특이사항을 기록/점검하는 데일리 체크리스트 |

<br>

## 3. UI / 화면 구성

- [화면정의서](https://sknetworks-family-aicamp.github.io/SKN31-4th-1Team/산출물/화면정의서.html)

- 실행화면

  ![서비스 데모](산출물/images/demo11.gif)



---
## 4. 디렉토리 구조

```
SKN31-4th-1Team/
├── .env                          # 환경변수 (API 키, DB 접속 정보 — git 미포함)
├── .gitignore
├── config.py                     # 프로젝트 전역 설정 상수 (모델명, 경로, DB 접속 정보 등)
├── eval.ipynb                    # RAGAS / 성능 평가용 Jupyter Notebook
├── README.md
├── requirements.txt
├── server.bat                    # 메인 실행 스크립트
├── UVon.bat                      # Virtualenv / UV 환경 활성화 스크립트
├── git_auto_uploader.bat         # Git 자동 업로드 스크립트
├── 통신 규격.md                  # 백엔드/프론트엔드 API 통신 명세서
│
├── graph_db/                     # GraphDB(Neo4j) 관련 코드 (크롤링/적재/TOOL)
│   ├── __init__.py
│   ├── cypher_prompt.py          # Cypher 쿼리 생성용 프롬프트 관리
│   ├── graph_search_tool.py      # Neo4j 그래프 검색 툴
│   ├── load_to_aura.py           # Neo4j Aura DB 데이터 적재
│   ├── preprocess.py             # 그래프 데이터 전처리
│   └── data/
│       
├── modules/                      # LangGraph 파이프라인 (에이전트, State, 그래프 구성)
│   ├── __init__.py
│   ├── agent.py                  # LangGraph 코어 파이프라인
│   └── extractor.py              # 엔티티 추출 모듈
│
├── server/                       # FastAPI 서버
│   ├── __init__.py
│   ├── agent.py                  # LangGraph 기반 ReAct 에이전트 및 프롬프트 로직
│   ├── auth.py                   # 사용자 인증 및 권한 관리
│   ├── context_loader.py         # 대화 컨텍스트 및 DB 데이터 로딩
│   ├── extractor.py              # 주관식 응답 데이터(엔티티) 추출 모듈
│   ├── family_tool.py            # 가족 관계 및 환자 기본 정보 조회 툴
│   ├── main.py                   # FastAPI 메인 애플리케이션 및 라우터
│   ├── server.bat                # 서버 실행(uvicorn) 스크립트
│   └── state_manager.py          # 세션 상태 및 Supabase 연동 관리
│
├── vector_db/                    # VectorDB(Qdrant) 관련 코드 (크롤링/적재/TOOL)
│   ├── __init__.py
│   ├── run_vector.py             # VectorDB 벡터화 및 임베딩 실행 스크립트
│   ├── vector_search_tool.py     # Qdrant 벡터 검색 툴
│   └── data/
│
└── 산출물/                       # 프로젝트 최종 산출물 및 문서
    ├── 데이터수집및전처리문서.md
    ├── 성능평가.md
    ├── 시스템아키텍쳐.md
    ├── 실행화면.md
    └── images/                   
```

---
## 5. 관련 문서

프로젝트의 상세한 정보 및 가이드는 아래 문서에서 확인하실 수 있습니다.

- [**📌 요구사항 정의서**](/산출물/요구사항정의서.md)
- [**🧩 화면 설계서**](/산출물/화면설계서.md)
- [**🏗️ 시스템 구성도**](/산출물/시스템아키텍쳐.md)
- [**📖 전체 테스트 계획서 및 테스트 결과보고서** ](/산출물/테스트설계및결과.md)
- [**📖 RAG성능평가** ](/산출물/성능평가.md)
- [**📖 데이터수집 및 전처리 문서** ](/산출물/데이터수집및전처리문서.md)

<br>

---
## 6. 회고

#### 구현 중 겪었던 문제와 해결 or 각자 느낀 점

#### 동민
- 

#### 연아
- 

#### 효민
- 

#### 진영
-

---

