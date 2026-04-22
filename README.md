# CRM 멀티에이전트 시스템

AI 기반 기관투자자 영업 지원 CRM. Claude 멀티에이전트 파이프라인이 고객 데이터와 영업 노트를 분석하여 고객 선호도 프로파일링, Next Best Action 추천, 활동 일정을 자동 생성합니다.

**라이브 데모**: [https://agenticcrm-production-cbeb.up.railway.app/](https://agenticcrm-production-cbeb.up.railway.app/)

---

## 주요 기능

- **독립 실행 에이전트** — Persona / NBA / Activity / QC 각각 개별 버튼으로 독립 실행, 또는 전체 파이프라인 일괄 실행
- **선택적 분석 범위** — Persona·NBA는 마지막 실행 이후 입력된 세일즈 노트만 참고하여 증분 업데이트
- **NBA 승인 워크플로우** — AI 제안 → CRM 담당자 승인 → 세일즈 담당자 승인 3단계 관리
- **고객 불만 징후 탐지** — `DislikeCheckerAgent`가 선택된 노트의 Action Point를 해당 고객 페르소나의 `explicit_dislikes`와 의미 기반으로 비교하여 위반 항목을 플래그. NBA 최우선 액션과의 비교 패널에도 빨간 경고로 강조
- **실시간 스트리밍** — SSE(Server-Sent Events)로 각 에이전트 실행 진행 상황 및 완료 시각 실시간 표시
- **분석 완료 시각 분 단위 표시** — Persona · NBA · Activity 각 섹션에 마지막 업데이트 타임스탬프(`YYYY-MM-DD HH:MM`) 노출
- **영업 노트 관리** — 새 스키마(Sales_ID, Customer_Feedback, Action_Point 등) 기반 웹 CRUD + 체크박스 벌크 삭제 (페르소나/NBA 연관 결과 유지)
- **세일즈 노트 CSV 일괄 업로드** — CSV 파싱 → 행별 검증(고객사 매칭, 필수 필드) → 유효 행만 DB 반영 2단계 워크플로우
- **고객 추가/삭제** — `고객 전체 조회` 탭에서 신규 등록(자동 채번 C0XX) 및 선택 벌크 삭제 (sales_notes/personas/NBA/activities/QC 캐스케이드)
- **멀티 모델 지원** — Claude Opus/Sonnet(Anthropic), Gemma/Llama/DeepSeek(OpenRouter 무료) 중 선택
- **자동 DB 전환** — 로컬은 SQLite, Railway 배포 시 PostgreSQL 자동 전환
- **대시보드 탭 뷰** — 고객 대시보드 / 고객 전체 조회 / 세일즈 노트 전체 조회 / 세일즈 노트 일괄 업로드 / 페르소나 전체 조회 탭 전환 (localStorage 유지)
- **고객 전체 조회** — 등급·유형·담당 영업·텍스트 검색 필터, 컬럼 정렬, 체크박스 선택, 상세 모달, 고객 추가/삭제 버튼
- **세일즈 노트 전체 조회** — 고객사·날짜 범위·활동 유형·섹터·담당자·텍스트 검색 필터, 컬럼 정렬, 체크박스 선택, 상세 모달, 노트 추가/삭제 버튼, 고객 불만 징후 탐지 버튼, `고객불만징후감지`/`감지 이유` 컬럼
- **페르소나 전체 조회** — 등급·텍스트 필터, 컬럼 정렬, 전체 페르소나 내용 상세 모달
- **노트 현황 표시** — 대시보드 카드에 고객별 영업 노트 건수 및 최근 날짜 표시

---

## 화면 구성

### 초기 화면 (탭 기반)

- **고객 대시보드 탭** — 고객별 등급·AUM·담당자 카드, 분석 완료 현황, 카드/리스트 뷰 전환, 모델 선택
- **고객 전체 조회 탭** — 전체 고객을 플랫 테이블로 조회, 등급·유형·담당 영업·텍스트 필터, 컬럼 정렬, 체크박스 선택. `고객 추가`(자동 채번) · `선택 삭제`(연관 분석 결과 캐스케이드 + 경고 다이얼로그) 지원
- **세일즈 노트 탭** — 전체 고객 노트 통합 조회, 고객사·날짜 범위·활동 유형·섹터·담당자·텍스트 필터, 컬럼 정렬, 체크박스 선택, 상세 모달. `노트 추가`(고객사 드롭다운) · `선택 삭제`(페르소나/NBA 유지) · `고객 불만 징후 탐지`(선택 노트에 대해 에이전트 실행) 버튼, `고객불만징후감지` 및 `감지 이유`(페르소나 원문 그대로) 컬럼
- **세일즈 노트 일괄 업로드 탭** — CSV 업로드 → 행별 파싱/검증 → 유효 행만 DB 반영 2단계 워크플로우
- **페르소나 탭** — 전체 고객 페르소나 조회, 등급·텍스트 필터, 컬럼 정렬, 상세 내용 모달

### 고객 상세 페이지
- **전체 분석 실행** — Persona → NBA → Activity → QC 순차 파이프라인 + 실시간 로그 터미널
- **영업 노트** — 과거 활동 기록 아코디언 목록 (Action_Point 강조 표시), 새 노트 추가 모달
- **Persona 섹션** — 고객 선호도 프로파일 + `Persona 업데이트` 버튼 + 마지막 업데이트 타임스탬프(분 단위)
- **NBA 섹션** — 우선순위별 영업 액션 + `NBA 제안` 버튼 + 참고 노트 비교 테이블 + 승인 상태 배지 + 마지막 제안 타임스탬프(분 단위). 최우선 액션 vs 노트 Action_Point 비교 패널에서 해당 노트가 페르소나 `explicit_dislikes`에 해당할 경우 **빨간색 경고 배너 + 카드 테두리 강조** 자동 표시
- **Activity 섹션** — Activity 일정표 + `Activity 업데이트` 버튼 + NBA 승인 상태 · 진행 상태 컬럼 + 마지막 업데이트 타임스탬프(분 단위)
- **QC 섹션** — 품질 검수 보고서 + `QC 검수 실행` 버튼

---

## 아키텍처

```
웹 브라우저
    │  SSE / REST API
    ▼
web/app.py (FastAPI)
    │
    ├── GET    /api/analyze/{id}               ── 전체 파이프라인 (SSE)
    ├── GET    /api/run/persona/{id}           ── Persona 단독 실행 (SSE, since_date 자동 적용)
    ├── GET    /api/run/nba/{id}               ── NBA 단독 실행 (SSE, since_date 자동 적용)
    ├── GET    /api/run/activity/{id}          ── Activity 단독 실행 (SSE)
    ├── GET    /api/run/qc/{id}                ── QC 단독 실행 (SSE)
    ├── GET    /api/customers                  ── 전체 고객 목록
    ├── POST   /api/customers                  ── 고객 신규 생성 (자동 채번)
    ├── DELETE /api/customers                  ── 고객 벌크 삭제 (연관 결과 캐스케이드)
    ├── GET    /api/sales-notes/{id}           ── 영업 노트 조회
    ├── POST   /api/sales-notes                ── 영업 노트 추가
    ├── DELETE /api/sales-notes                ── 영업 노트 벌크 삭제 (no cascade)
    ├── POST   /api/sales-notes/upload         ── CSV 파싱 + 행별 검증
    ├── POST   /api/sales-notes/bulk-commit    ── 유효 행만 DB 반영
    └── POST   /api/sales-notes/check-dislikes ── 선택 노트의 페르소나 불만 위반 탐지
              │
    ┌─────────┴──────────────────────┐
    ▼                                ▼
OrchestratorAgent (전체 실행)   개별 Agent 직접 호출
    │ Claude tool_use
    ├─ PersonaAgent          ←── Customer_Feedback만 분석, since_date 필터
    ├─ NBAAgent              ←── 최근 3개월 recency 가중치, since_date 필터
    ├─ ActivityAgent         ←── NBA 승인 상태 미러링, Activity 진행 상태 관리
    ├─ QCAgent               ←── 전체 에이전트 출력 품질 검수
    └─ DislikeCheckerAgent   ←── 고객별 배치로 Action_Point vs explicit_dislikes 판정
              │
     src/tools/data_tools.py
              │
  ┌───────────┴───────────┐
DB (SQLite/PostgreSQL)  data/*.json
customers               customers.json (11개 고객, DB upsert)
personas, nba_results   sales_notes.json (새 스키마, DB 시드)
activities, qc_reports  action_plans.json
sales_notes             ※ sales_notes.data JSON에 _red_flag 메타 영속
```

### 에이전트 역할

| 에이전트 | 입력 | 분석 내용 | 출력 |
|---|---|---|---|
| **PersonaAgent** | `Customer_Feedback` 필드 (since_date 이후) | 선호 섹터·콘텐츠 유형·분석 스타일·명시적 요구사항 | 고객 선호도 프로파일 |
| **NBAAgent** | 페르소나 + 최근 3개월 노트 (recency 가중치) | 우선순위별 영업 액션, 구체적 기한(날짜), 최근 Action_Point 비교 | NBA 추천 + 승인 워크플로우 |
| **ActivityAgent** | NBA 추천 결과 | 구체적 실행 일정, NBA 승인 상태 반영, Activity 진행 상태 | Activity 스케줄 |
| **QCAgent** | 모든 에이전트 최신 출력 | 일관성·완결성·품질 검수 | QC 보고서 (pass/fail + 점수) |
| **DislikeCheckerAgent** | 페르소나 `explicit_dislikes` + 선택된 노트의 `Action_Point` (고객별 배치) | Action_Point가 고객이 명시한 불만/거부 패턴에 해당하는지 의미 기반 매칭 | 노트별 `{is_red_flag, matched_dislike, reason}` — `sales_notes.data`에 영속 |

---

## 세일즈 노트 스키마

```json
{
  "Sales_ID": "S01",
  "Sales_Name": "김영민 / 부장",
  "Activity_Date": "2026-04-12",
  "Client_Type": "운용사",
  "Client_Name": "미래에셋자산운용",
  "Contact_Role": "펀드매니저",
  "Contact_Name": "박지훈 / 팀장",
  "Sector": "반도체",
  "Activity_Type": "대면미팅",
  "Activity_Log": "[대면미팅 - 반도체] 미팅 내용...",
  "Customer_Feedback": "[미래에셋] 고객이 표현한 피드백...",
  "Action_Point": "후속 조치 내용...",
  "Language": "KR"
}
```

> `Customer_Feedback` — PersonaAgent의 유일한 분석 소스  
> `Action_Point` — NBAAgent가 가장 우선 참고하는 필드 · DislikeCheckerAgent의 판정 대상

**시스템 메타 필드** (DislikeCheckerAgent 실행 후 자동 추가):

| 필드 | 설명 |
|---|---|
| `_red_flag` | Action Point가 페르소나 불만/거부 항목에 해당하면 `true` |
| `_red_flag_matched` | 매칭된 `explicit_dislikes` 항목 원문 |
| `_red_flag_reason` | 판정 근거 한 문장 |
| `_red_flag_checked_at` | 탐지 실행 타임스탬프 (`YYYY-MM-DD HH:MM`) |

---

## NBA 승인 워크플로우

NBA 추천 결과의 각 액션은 3단계 승인 상태를 가집니다:

| 상태 | 설명 | 배지 색상 |
|---|---|---|
| `ai_proposed` | AI가 NBA를 제안한 초기 상태 | 노랑 |
| `crm_approved` | CRM 담당자가 검토·승인한 상태 | 파랑 |
| `sales_approved` | 세일즈 담당자가 최종 승인, 즉시 실행 가능 | 초록 |

Activity 섹션에서 각 Activity의 NBA 승인 상태와 진행 상태(`pending` / `in_progress` / `completed` / `cancelled`)를 함께 확인할 수 있습니다.

---

## 고객 불만 징후 탐지 워크플로우

`DislikeCheckerAgent`는 영업 담당자가 작성한 `Action Point`가 해당 고객이 과거에 **명시적으로 거부/불만을 표현한 항목**과 실제로 충돌하는지를 판정합니다.

**실행 흐름**
1. `세일즈 노트 전체 조회` 탭에서 노트를 체크박스로 선택
2. `고객 불만 징후 탐지` 버튼 클릭 → `POST /api/sales-notes/check-dislikes`
3. 서버는 선택 노트를 고객별로 그룹화 → 고객당 1회씩 에이전트 호출 (페르소나가 없거나 `explicit_dislikes`가 비어있으면 스킵)
4. 결과를 각 노트의 `_red_flag*` 필드로 영속 → 테이블 재렌더링

**결과 표시**
- **테이블** — `고객불만징후감지` 컬럼에 체크 표시, `감지 이유` 컬럼에 매칭된 페르소나 원문 노출, 행 배경을 빨간색(`#ffcfcf`)으로 강조
- **고객 상세 페이지 NBA 섹션** — NBA의 `top_priority_comparison.note_id`가 플래그된 노트일 경우 비교 패널에 빨간 경고 배너 + 카드 테두리 강조 자동 표시 (추가 LLM 호출 없이 영속된 메타를 재활용)

---

## 빠른 시작

### 사전 요구사항

- Python 3.11+
- Anthropic API 키 ([console.anthropic.com](https://console.anthropic.com))

### 설치

```bash
git clone https://github.com/fabelian/agenticCRM.git
cd agenticCRM

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 환경 설정

프로젝트 루트에 `.env` 파일 생성:

```env
ANTHROPIC_API_KEY=sk-ant-...

# OpenRouter 무료 모델 사용 시 (선택)
OPENROUTER_API_KEY=sk-or-...
```

### 웹 서버 실행

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 접속

### CLI 실행

```bash
# 단일 고객 전체 파이프라인 분석
python src/main.py C001

# 커스텀 태스크 지정
python src/main.py C001 --task "반도체 섹터 집중 분석만 수행해주세요"

# 전체 고객 순차 분석
python src/main.py --all
```

---

## API 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/customers` | 전체 고객 목록 |
| `POST` | `/api/customers` | 고객 신규 생성 (customer_id 미지정 시 자동 채번 C0XX) |
| `DELETE` | `/api/customers` | 고객 벌크 삭제 — sales_notes/personas/nba_results/activities/qc_reports 캐스케이드 |
| `GET` | `/api/customer/{id}` | 고객 분석 결과 조회 (persona/nba/activities/qc + `activities_updated_at`) |
| `GET` | `/api/analyze/{id}` | 전체 파이프라인 실행 — SSE |
| `GET` | `/api/run/persona/{id}` | Persona 단독 실행 — SSE (마지막 업데이트 이후 노트만 분석) |
| `GET` | `/api/run/nba/{id}` | NBA 단독 실행 — SSE (마지막 제안 이후 노트만 분석) |
| `GET` | `/api/run/activity/{id}` | Activity 단독 실행 — SSE |
| `GET` | `/api/run/qc/{id}` | QC 단독 실행 — SSE |
| `GET` | `/api/sales-notes/{id}` | 고객별 영업 노트 목록 |
| `POST` | `/api/sales-notes` | 새 영업 노트 추가 |
| `DELETE` | `/api/sales-notes` | 영업 노트 벌크 삭제 (연관 페르소나/NBA 유지 — **no cascade**) |
| `POST` | `/api/sales-notes/upload` | CSV 파일 파싱 + 행별 검증 (DB 저장 없음) |
| `POST` | `/api/sales-notes/bulk-commit` | upload에서 검증된 유효 행만 DB insert |
| `POST` | `/api/sales-notes/check-dislikes` | 선택 노트의 페르소나 불만 위반 탐지 — DislikeCheckerAgent 실행 + 결과 영속 |
| `GET` | `/api/all-sales-notes` | 전체 고객 영업 노트 통합 조회 (날짜 내림차순) |
| `GET` | `/api/all-personas` | 전체 고객 페르소나 조회 (고객 정보 포함) |
| `GET` | `/api/models` | 사용 가능 모델 목록 |
| `POST` | `/api/model` | 분석 모델 변경 |
| `GET` | `/api/debug` | DB 상태 진단 — 테이블 목록, 행 수, customers.json 확인 |
| `GET` | `/api/debug/env` | 환경 변수 진단 — DATABASE_URL(마스킹), Railway 환경변수 |

### SSE 이벤트 형식

각 SSE 엔드포인트는 아래 3가지 이벤트를 스트리밍합니다:

```json
{"type": "log",   "text": "에이전트 실행 로그..."}
{"type": "done",  "completed_at": "2026-04-17 14:32:05"}
{"type": "error", "text": "오류 메시지..."}
```

---

## 프로젝트 구조

```
agenticCRM/
├── data/
│   ├── customers.json        # 고객 기본 정보 (읽기 전용)
│   ├── sales_notes.json      # 영업 노트 초기 데이터 (DB 시드용, 새 스키마)
│   └── action_plans.json     # 기존 액션 플랜 (읽기 전용)
├── src/
│   ├── agents/
│   │   ├── base_agent.py             # 공통 Agentic Loop (max_tokens 연속 생성, OpenRouter 오류 가드)
│   │   ├── orchestrator.py           # 전체 파이프라인 조율
│   │   ├── persona_agent.py          # Customer_Feedback 기반 선호도 분석
│   │   ├── nba_agent.py              # recency 가중치 NBA 추천 + 승인 워크플로우
│   │   ├── activity_agent.py         # NBA → Activity 변환 + 진행 상태 관리
│   │   ├── qc_agent.py               # 품질 검수
│   │   └── dislike_checker_agent.py  # 선택 노트 Action_Point ↔ 페르소나 explicit_dislikes 매칭
│   ├── db/
│   │   └── database.py       # SQLAlchemy 모델 & 엔진
│   ├── tools/
│   │   ├── data_tools.py     # 데이터 액세스 레이어 (since_date 필터 지원)
│   │   └── openrouter_client.py
│   └── main.py               # CLI 진입점
├── web/
│   ├── app.py                # FastAPI 앱 & API 라우트 (개별 에이전트 SSE 포함)
│   └── templates/
│       ├── index.html        # 고객 대시보드
│       └── customer.html     # 고객 상세 — 4개 독립 섹션 카드
├── output/                   # 분석 보고서 저장 (.gitignore)
├── crm.db                    # SQLite DB (.gitignore)
├── sample_c002_dislike_action_points.csv  # 불만 탐지 테스트용 CSV 픽스처 (C002, 5건)
├── test_sales_notes_uploads.csv           # CSV 일괄 업로드 테스트 픽스처
├── .env.example
└── requirements.txt
```

---

## 지원 모델

| 모델 | 제공사 | 특징 |
|---|---|---|
| Claude Opus 4.6 | Anthropic | 최고 성능 (기본값) |
| Claude Sonnet 4.6 | Anthropic | 빠른 속도 · 낮은 비용 |
| Gemma 4 26B | OpenRouter (무료) | rate limit 있음 |
| Llama 4 Scout | OpenRouter (무료) | rate limit 있음 |
| DeepSeek V3 | OpenRouter (무료) | rate limit 있음 |
| MiniMax M2.5 | OpenRouter (무료) | rate limit 있음 |

---

## 배포 (Railway)

1. Railway 프로젝트에 이 저장소 연결
2. 환경변수 설정:

| 변수 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 필수 | Anthropic API 키 |
| `DATABASE_URL` | 필수 | Railway PostgreSQL 자동 제공 |
| `OPENROUTER_API_KEY` | 선택 | OpenRouter 무료 모델 사용 시 |

3. Start Command: `uvicorn web.app:app --host 0.0.0.0 --port $PORT`

**DB 시딩 방식**: 앱 시작 시 `customers.json`, `sales_notes.json`, `personas.json`을 PostgreSQL에 자동 삽입합니다. customers 테이블은 `psycopg2`로 직접 upsert하여 Railway 환경에서의 안정성을 높였습니다.

**배포 후 진단**: 아래 엔드포인트로 DB 상태를 확인할 수 있습니다.
```
GET /api/debug      ← 테이블 행 수, customers.json 존재 여부
GET /api/debug/env  ← DATABASE_URL 마스킹, Railway 환경변수
```

---

## 기술 스택

- **Backend**: FastAPI, SQLAlchemy, Uvicorn
- **AI**: Anthropic Claude API (tool_use), OpenRouter
- **DB**: SQLite (로컬) / PostgreSQL (프로덕션)
- **Frontend**: Bootstrap 5.3, Bootstrap Icons, Vanilla JS (SSE)
- **Config**: python-dotenv
