# CRM 멀티에이전트 시스템

AI 기반 기관투자자 영업 지원 CRM. Claude 멀티에이전트 파이프라인이 고객 데이터와 영업 노트를 분석하여 고객 선호도 프로파일링, Next Best Action 추천, 활동 일정을 자동 생성합니다.

---

## 주요 기능

- **독립 실행 에이전트** — Persona / NBA / Activity / QC 각각 개별 버튼으로 독립 실행, 또는 전체 파이프라인 일괄 실행
- **선택적 분석 범위** — Persona·NBA는 마지막 실행 이후 입력된 세일즈 노트만 참고하여 증분 업데이트
- **NBA 승인 워크플로우** — AI 제안 → CRM 담당자 승인 → 세일즈 담당자 승인 3단계 관리
- **실시간 스트리밍** — SSE(Server-Sent Events)로 각 에이전트 실행 진행 상황 및 완료 시각 실시간 표시
- **영업 노트 관리** — 새 스키마(Sales_ID, Customer_Feedback, Action_Point 등) 기반 웹 CRUD
- **멀티 모델 지원** — Claude Opus/Sonnet(Anthropic), Gemma/Llama/DeepSeek(OpenRouter 무료) 중 선택
- **자동 DB 전환** — 로컬은 SQLite, Railway 배포 시 PostgreSQL 자동 전환
- **대시보드 카드/리스트 뷰** — 카드 전체 클릭 시 상세 페이지 이동, 카드·리스트 뷰 전환 (localStorage 유지)
- **노트 현황 표시** — 대시보드 카드에 고객별 영업 노트 건수 및 최근 날짜 표시

---

## 스크린샷

### 고객 대시보드
고객별 등급·AUM·담당자 카드, 분석 완료 현황, 모델 선택

### 고객 상세 페이지
- **전체 분석 실행** — Persona → NBA → Activity → QC 순차 파이프라인 + 실시간 로그 터미널
- **영업 노트** — 과거 활동 기록 아코디언 목록 (Action_Point 강조 표시), 새 노트 추가 모달
- **Persona 섹션** — 고객 선호도 프로파일 + `Persona 업데이트` 버튼 (마지막 업데이트 이후 노트만 분석)
- **NBA 섹션** — 우선순위별 영업 액션 + `NBA 제안` 버튼 + 참고 노트 비교 테이블 + 승인 상태 배지
- **Activity 섹션** — Activity 일정표 + `Activity 업데이트` 버튼 + NBA 승인 상태 · 진행 상태 컬럼
- **QC 섹션** — 품질 검수 보고서 + `QC 검수 실행` 버튼

---

## 아키텍처

```
웹 브라우저
    │  SSE / REST API
    ▼
web/app.py (FastAPI)
    │
    ├── GET /api/analyze/{id}          ── 전체 파이프라인 (SSE)
    ├── GET /api/run/persona/{id}      ── Persona 단독 실행 (SSE, since_date 자동 적용)
    ├── GET /api/run/nba/{id}          ── NBA 단독 실행 (SSE, since_date 자동 적용)
    ├── GET /api/run/activity/{id}     ── Activity 단독 실행 (SSE)
    ├── GET /api/run/qc/{id}           ── QC 단독 실행 (SSE)
    ├── GET /api/sales-notes/{id}      ── 영업 노트 조회
    └── POST /api/sales-notes          ── 영업 노트 추가
              │
    ┌─────────┴──────────────────────┐
    ▼                                ▼
OrchestratorAgent (전체 실행)   개별 Agent 직접 호출
    │ Claude tool_use
    ├─ PersonaAgent   ←── Customer_Feedback만 분석, since_date 필터
    ├─ NBAAgent       ←── 최근 3개월 recency 가중치, since_date 필터
    ├─ ActivityAgent  ←── NBA 승인 상태 미러링, Activity 진행 상태 관리
    └─ QCAgent        ←── 전체 에이전트 출력 품질 검수
              │
     src/tools/data_tools.py
              │
  ┌───────────┴───────────┐
DB (SQLite/PostgreSQL)  data/*.json
customers               customers.json (11개 고객)
personas, nba_results   sales_notes.json (새 스키마, ~30건)
activities, qc_reports  action_plans.json
sales_notes
```

### 에이전트 역할

| 에이전트 | 입력 | 분석 내용 | 출력 |
|---|---|---|---|
| **PersonaAgent** | `Customer_Feedback` 필드 (since_date 이후) | 선호 섹터·콘텐츠 유형·분석 스타일·명시적 요구사항 | 고객 선호도 프로파일 |
| **NBAAgent** | 페르소나 + 최근 3개월 노트 (recency 가중치) | 우선순위별 영업 액션, 구체적 기한(날짜), 최근 Action_Point 비교 | NBA 추천 + 승인 워크플로우 |
| **ActivityAgent** | NBA 추천 결과 | 구체적 실행 일정, NBA 승인 상태 반영, Activity 진행 상태 | Activity 스케줄 |
| **QCAgent** | 모든 에이전트 최신 출력 | 일관성·완결성·품질 검수 | QC 보고서 (pass/fail + 점수) |

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
> `Action_Point` — NBAAgent가 가장 우선 참고하는 필드

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
| `GET` | `/api/customer/{id}` | 고객 분석 결과 조회 |
| `GET` | `/api/analyze/{id}` | 전체 파이프라인 실행 — SSE |
| `GET` | `/api/run/persona/{id}` | Persona 단독 실행 — SSE (마지막 업데이트 이후 노트만 분석) |
| `GET` | `/api/run/nba/{id}` | NBA 단독 실행 — SSE (마지막 제안 이후 노트만 분석) |
| `GET` | `/api/run/activity/{id}` | Activity 단독 실행 — SSE |
| `GET` | `/api/run/qc/{id}` | QC 단독 실행 — SSE |
| `GET` | `/api/sales-notes/{id}` | 고객별 영업 노트 목록 |
| `POST` | `/api/sales-notes` | 새 영업 노트 추가 |
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
│   │   ├── base_agent.py     # 공통 Agentic Loop (max_tokens 연속 생성)
│   │   ├── orchestrator.py   # 전체 파이프라인 조율
│   │   ├── persona_agent.py  # Customer_Feedback 기반 선호도 분석
│   │   ├── nba_agent.py      # recency 가중치 NBA 추천 + 승인 워크플로우
│   │   ├── activity_agent.py # NBA → Activity 변환 + 진행 상태 관리
│   │   └── qc_agent.py       # 품질 검수
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
