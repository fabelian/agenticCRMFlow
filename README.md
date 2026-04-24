# CRM 멀티에이전트 시스템

AI 기반 기관투자자 영업 지원 CRM. Claude 멀티에이전트 파이프라인이 고객 데이터와 영업 노트를 분석하여 고객 선호도 프로파일링, Next Best Action 추천, 활동 일정 생성, 품질 검수를 자동 수행합니다.

**라이브 데모**: [https://agenticcrm-production-cbeb.up.railway.app/](https://agenticcrm-production-cbeb.up.railway.app/)

---

## 주요 기능

### 에이전트 실행
- **개별 실행** — 고객 상세 페이지에서 Persona / NBA / Activity / QC 각각 독립 버튼으로 실행, 또는 전체 파이프라인 일괄 실행
- **대시보드 일괄 실행** — 전체 고객 대상 `Persona / NBA / Activity / QC` 4종 일괄 업데이트 버튼. SSE로 고객별 진행 상황 · 성공 · 실패 · 스킵을 실시간 표시
- **의존성 자동 스킵** — NBA는 Persona가 없으면, Activity는 NBA가 없으면, QC는 Persona/NBA/Activity 중 하나라도 없으면 해당 고객을 자동 스킵하고 누락 항목을 명시
- **증분 업데이트** — Persona·NBA는 마지막 실행 이후 추가된 노트만 반영하는 증분 모드(기본) + 전체 재생성(`강제 재생성` 체크박스) 지원

### 조회 화면 (탭 구조)
- **고객 대시보드** — 카드/리스트 뷰, AI Chat 사이드바
- **고객 전체 조회** — 플랫 테이블, 다축 필터, 컬럼 정렬, 벌크 삭제
- **세일즈 노트 전체 조회** — 고객사·날짜·활동 유형·섹터·담당자·텍스트 필터, 불만 징후 탐지 결과 컬럼
- **세일즈 노트 일괄 업로드** — CSV 파싱 → 행별 검증 → 유효 행만 DB 반영 2단계
- **페르소나 전체 조회** — 테이블/상세 스택 뷰, 사용자 선택 컬럼
- **전체 NBA 추천** — 고객별 카드 스택. 최우선 액션 vs 노트 Action_Point 비교 + RED FLAG 경고(고객 상세와 동일 스타일)
- **전체 Activity** — 리스트 뷰(컬럼 클릭 정렬, 기본 기한 오름차순) / 월 달력 뷰 토글. 필터: 고객·기한 from-to·진행 상태·NBA 승인 상태. 달력 셀은 하루 최대 4개 + `+N 더보기`로 전체 리스트 모달. 아이템 클릭 시 Activity 상세 모달
- **전체 QC 검수** — 카드 스택. 필터: 검색·등급·판정·최소 점수. 정렬: 최근 검수 / 점수 낮은순 / 점수 높은순. 부문별 점수 · critical issues · 재처리 필요 배지

### AI 어시스턴트
- **CRM 챗 사이드바** — 대시보드 우측 패널. `ChatAgent`가 읽기 전용 도구(고객·페르소나·NBA·Activity·QC·세일즈 노트 조회)로 질문에 맞는 데이터를 끌어와 답변. 서버는 무상태 — 대화 이력은 클라이언트가 유지
- **멀티 모델** — Claude Opus/Sonnet(Anthropic), OpenRouter 무료 모델(Gemma·GPT-OSS·GLM·MiniMax) 선택

### 데이터 품질
- **NBA 승인 워크플로우** — AI 제안 → CRM 승인 → Sales 승인 3단계. 각 Activity는 연결된 NBA 액션의 승인 상태를 미러링
- **고객 불만 징후 탐지** — `DislikeCheckerAgent`가 선택 노트의 `Action_Point`를 페르소나 `explicit_dislikes`와 의미 매칭해 RED FLAG 플래그. 결과는 노트에 영속되어 테이블 행 하이라이트 + NBA 비교 패널 경고로 재사용
- **QC 보고서** — 페르소나 / NBA / Activity / 일관성 각 부문별 점수 + 판정(pass/fail) + critical issues

### 시스템
- **실시간 스트리밍** — 모든 에이전트 실행은 SSE로 진행 로그 · 완료 시각 실시간 전달
- **KST 타임스탬프** — 모든 사용자 노출 시각은 한국 표준시(UTC+9), 분 단위 표시
- **자동 DB 전환** — 로컬 SQLite ↔ Railway PostgreSQL 자동
- **탭 상태 유지** — localStorage로 마지막 탭 복원, 탭별 lazy load + 캐시 무효화

---

## 아키텍처

```
웹 브라우저
    │  SSE / REST API
    ▼
web/app.py (FastAPI)
    │
    ├── 개별 에이전트 SSE
    │   ├── GET /api/analyze/{id}         ── 전체 파이프라인 (Orchestrator)
    │   ├── GET /api/run/persona/{id}     ── Persona 단독 (since_date)
    │   ├── GET /api/run/nba/{id}         ── NBA 단독 (since_date)
    │   ├── GET /api/run/activity/{id}    ── Activity 단독
    │   └── GET /api/run/qc/{id}          ── QC 단독
    │
    ├── 전체 고객 일괄 SSE
    │   ├── GET /api/run/persona-all      ── 페르소나 일괄 (force 옵션)
    │   ├── GET /api/run/nba-all          ── NBA 일괄 (force 옵션, Persona 미생성 스킵)
    │   ├── GET /api/run/activity-all     ── Activity 일괄 (NBA 미생성 스킵)
    │   └── GET /api/run/qc-all           ── QC 일괄 (Persona/NBA/Activity 중 하나라도 없으면 스킵)
    │
    ├── 전체 조회
    │   ├── GET /api/all-personas         ── 전체 페르소나 + 고객 메타
    │   ├── GET /api/all-nba              ── 전체 NBA + 고객 메타 + 매칭 노트 RED FLAG 조인
    │   ├── GET /api/all-activities       ── 전체 Activity 플래튼 (기한 오름차순)
    │   ├── GET /api/all-qc               ── 전체 QC (reviewed_at 내림차순)
    │   └── GET /api/all-sales-notes      ── 전체 노트 통합 (날짜 내림차순)
    │
    ├── CRUD
    │   ├── GET|POST|DELETE /api/customers                ── 고객 (자동 채번, 캐스케이드 삭제)
    │   ├── GET|POST|DELETE /api/sales-notes              ── 노트 (페르소나/NBA 유지 삭제)
    │   ├── POST /api/sales-notes/upload                  ── CSV 파싱 + 행별 검증
    │   ├── POST /api/sales-notes/bulk-commit             ── 유효 행만 DB insert
    │   └── POST /api/sales-notes/check-dislikes          ── DislikeCheckerAgent 실행 + 영속
    │
    └── AI Chat / 모델
        ├── POST /api/chat                 ── ChatAgent (tool use, 무상태)
        ├── GET|POST /api/model(s)         ── 모델 레지스트리
        └── GET /api/debug(/env)           ── DB/환경 변수 진단
              │
    ┌─────────┴──────────────────────┐
    ▼                                ▼
OrchestratorAgent (전체 실행)    개별 Agent 직접 호출
    │ Claude tool_use
    ├─ PersonaAgent           ←── Customer_Feedback만 분석, since_date 필터
    ├─ NBAAgent               ←── 최근 3개월 recency 가중치, since_date 필터
    ├─ ActivityAgent          ←── NBA 승인 상태 미러링, 진행 상태 관리
    ├─ QCAgent                ←── 전체 에이전트 출력 검수 (점수+판정)
    ├─ DislikeCheckerAgent    ←── 고객별 배치, Action_Point ↔ explicit_dislikes
    └─ ChatAgent              ←── 읽기 전용 다중 도구로 자연어 질의 응답
              │
     src/tools/data_tools.py
              │
  ┌───────────┴───────────┐
DB (SQLite/PostgreSQL)  data/*.json
customers               customers.json (DB upsert)
personas, nba_results   sales_notes.json (시드)
activities, qc_reports  action_plans.json (읽기 전용)
sales_notes             ※ sales_notes.data JSON에 _red_flag 메타 영속
```

### 에이전트 역할

| 에이전트 | 입력 | 분석 내용 | 출력 |
|---|---|---|---|
| **PersonaAgent** | `Customer_Feedback` (since_date 이후) | 선호 섹터·콘텐츠 유형·분석 스타일·명시적 요구사항·explicit_dislikes | 고객 선호도 프로파일 |
| **NBAAgent** | 페르소나 + 최근 3개월 노트 (recency 가중치) | 우선순위별 영업 액션, 구체적 기한(YYYY-MM-DD), `top_priority_comparison` | NBA 추천 + 3단계 승인 워크플로우 |
| **ActivityAgent** | NBA 추천 결과 | 구체적 실행 일정, NBA 승인 상태 미러링, 진행 상태(pending/in_progress/completed/cancelled) | Activity 스케줄 |
| **QCAgent** | 모든 에이전트 최신 출력 | 페르소나/NBA/Activity/일관성 부문별 점수, critical issues | QC 보고서 (0~100점 + verdict) |
| **DislikeCheckerAgent** | 페르소나 `explicit_dislikes` + 선택 노트의 `Action_Point` (고객별 배치) | 의미 기반 매칭 | 노트별 `{is_red_flag, matched_dislike, reason}` — `sales_notes.data`에 영속 |
| **ChatAgent** | 사용자 자연어 질문 + 읽기 전용 조회 도구 (전체 고객/페르소나/NBA/Activity/QC/노트) | 질문 의도에 맞는 도구를 선택·호출 후 답변 합성 | 대시보드 사이드바 응답 (무상태) |

---

## 화면 구성

### 대시보드 탭
- **고객 대시보드** — 카드/리스트 뷰 토글, 등급·AUM·담당자·노트 건수 표시
  - 상단 일괄 실행 버튼 4종: `[전체 페르소나 업데이트(primary)] [전체 NBA 추천(warning)] [전체 Activity 업데이트(success)] [전체 QC 검수(info)]` — 각 버튼 옆에 `강제 재생성` 체크박스(해당되는 에이전트만)
  - 각 버튼 클릭 시 진행 패널이 나타나 고객별 상태(분석 중/완료/스킵/오류)와 전체 진행률 바를 실시간 표시
  - 우측 AI Chat 사이드바
- **고객 전체 조회** — 플랫 테이블, 등급·유형·담당 영업·텍스트 필터, 컬럼 정렬, 체크박스 선택, `고객 추가`(자동 채번 `C0XX`) · `선택 삭제`(연관 분석 결과 캐스케이드)
- **세일즈 노트 전체 조회** — 고객사·날짜 범위·활동 유형·섹터·담당자·텍스트 필터, 컬럼 정렬, 체크박스 선택, 상세 모달, `노트 추가` · `선택 삭제`(페르소나/NBA 유지) · `고객 불만 징후 탐지` 버튼, `고객불만징후감지` 및 `감지 이유` 컬럼
- **세일즈 노트 일괄 업로드** — CSV 업로드 → 행별 파싱/검증 → 유효 행만 DB 반영
- **페르소나 전체 조회** — 테이블/상세 스택 뷰 토글, 등급·텍스트 필터, 사용자 선택 컬럼(최대 3개)
- **전체 NBA 추천** — 고객별 카드 스택. 카드: 회사명 · tier · 위험도 배지 · 분석일 · 요약 · **최우선 액션 vs 노트 Action_Point 비교(3컬럼 테이블)** · 추천 액션 표 · 피할 것 · 예상 성과 · 참고 노트. 매칭 노트가 RED FLAG인 경우 경고 배너 + 빨간 테두리 자동 표시
- **전체 Activity** — 리스트/달력 뷰 토글(기본 리스트)
  - 리스트: 기한/고객사/ID/제목/유형/진행상태/NBA 승인/연계 NBA 컬럼. **헤더 클릭 정렬**(같은 컬럼 다시 클릭 시 방향 토글). 한국어 locale 비교로 고객사 정렬
  - 달력: 월 뷰. 셀당 최대 4개 activity(유형별 보더 컬러) + `+N 더보기`로 날짜별 전체 리스트 모달. 아이템 클릭 → Activity 상세 모달
  - 필터: 고객사 · 기한 From/To · 진행 상태 · NBA 승인 상태 (상태·승인 값은 DB 실제 값에서 자동 추출)
- **전체 QC 검수** — 카드 스택. 카드 헤더: tier/회사명/판정 배지/재처리 필요 배지/검수 시각/상세 링크. 본문: 큰 점수 circle + 부문별 점수 · 종합 평가 · critical issues(severity별 색상). 필터: 검색·등급·판정·최소 점수. 정렬: 최근 검수 / 점수 낮은순 / 점수 높은순

### 고객 상세 페이지
- **전체 분석 실행** — Persona → NBA → Activity → QC 순차 파이프라인 + 실시간 로그 터미널
- **영업 노트** — 과거 활동 기록 아코디언 (Action_Point 강조), 새 노트 추가 모달
- **Persona 섹션** — 고객 선호도 프로파일, `Persona 업데이트`, 마지막 업데이트 KST 타임스탬프
- **NBA 섹션** — 우선순위별 영업 액션, `NBA 제안`, 참고 노트 비교 테이블, 승인 상태 배지. 최우선 액션 vs 노트 비교 패널에서 해당 노트가 RED FLAG인 경우 빨간 경고 배너 + 카드 테두리 강조
- **Activity 섹션** — Activity 일정표, `Activity 업데이트`, NBA 승인 상태 · 진행 상태 컬럼
- **QC 섹션** — 품질 검수 보고서, `QC 검수 실행`, 점수 circle + 판정 배지 + critical issues

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
| `_red_flag_checked_at` | 탐지 실행 타임스탬프 (KST) |

---

## NBA 승인 워크플로우

NBA 추천 결과의 각 액션은 3단계 승인 상태를 가집니다:

| 상태 | 설명 | 배지 색상 |
|---|---|---|
| `ai_proposed` | AI가 NBA를 제안한 초기 상태 | 노랑 |
| `crm_approved` | CRM 담당자가 검토·승인한 상태 | 파랑 |
| `sales_approved` | 세일즈 담당자가 최종 승인, 즉시 실행 가능 | 초록 |

Activity 섹션의 각 Activity는 연결된 NBA 액션의 승인 상태를 그대로 미러링하며, 진행 상태(`pending` / `in_progress` / `completed` / `cancelled`)를 별도로 관리합니다.

---

## 고객 불만 징후 탐지 워크플로우

`DislikeCheckerAgent`는 영업 담당자가 작성한 `Action Point`가 해당 고객이 과거에 **명시적으로 거부/불만을 표현한 항목**과 실제로 충돌하는지를 판정합니다.

**실행 흐름**
1. `세일즈 노트 전체 조회` 탭에서 노트를 체크박스로 선택
2. `고객 불만 징후 탐지` 버튼 클릭 → `POST /api/sales-notes/check-dislikes`
3. 서버는 선택 노트를 고객별로 그룹화 → 고객당 1회씩 에이전트 호출 (페르소나가 없거나 `explicit_dislikes`가 비어있으면 스킵)
4. 결과를 각 노트의 `_red_flag*` 필드로 영속 → 테이블 재렌더링

**결과 표시**
- **세일즈 노트 테이블** — 행 배경 빨간색 강조, `고객불만징후감지`·`감지 이유` 컬럼
- **고객 상세 NBA 섹션** — `top_priority_comparison.note_id`가 플래그된 노트일 경우 비교 패널에 빨간 경고 배너 + 카드 테두리
- **전체 NBA 탭** — 동일 비교 스타일. 백엔드 `/api/all-nba`가 매칭 노트의 `_red_flag` 메타를 NBA 레코드에 조인해 내려줌 (추가 LLM 호출 없음)

---

## QC 검수 보고서 스키마

```json
{
  "overall_score": 0,
  "verdict": "pass_excellent | pass_good | conditional_pass | fail",
  "reviewed_at": "2026-04-24 14:32",
  "persona_review":    { "score": 0, "strengths": [], "issues": [], "recommendations": [] },
  "nba_review":        { "score": 0, "strengths": [], "issues": [], "recommendations": [] },
  "activity_review":   { "score": 0, "strengths": [], "issues": [], "recommendations": [] },
  "consistency_review":{ "score": 0, "issues": [] },
  "critical_issues":   [ { "severity": "critical|major|minor", "description": "", "fix": "" } ],
  "overall_summary": "",
  "reprocess_required": false
}
```

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

# Railway 배포 시 자동 설정 (로컬은 SQLite 자동 사용)
# DATABASE_URL=postgresql://...
```

### 웹 서버 실행

```bash
# 개발 (자동 리로드)
uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload

# 프로덕션
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

### 개별 고객 실행 (SSE)

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/analyze/{id}` | 전체 파이프라인 (Orchestrator) |
| `GET` | `/api/run/persona/{id}?force=...` | Persona 단독 (기본 증분, `force=true`면 전체 재생성) |
| `GET` | `/api/run/nba/{id}?force=...` | NBA 단독 (기본 증분) |
| `GET` | `/api/run/activity/{id}` | Activity 단독 |
| `GET` | `/api/run/qc/{id}` | QC 단독 |

### 전체 고객 일괄 실행 (SSE)

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/run/persona-all?force=...` | 전체 페르소나 일괄 |
| `GET` | `/api/run/nba-all?force=...` | 전체 NBA 일괄 (Persona 미생성 스킵) |
| `GET` | `/api/run/activity-all` | 전체 Activity 일괄 (NBA 미생성 스킵) |
| `GET` | `/api/run/qc-all` | 전체 QC 일괄 (Persona/NBA/Activity 중 하나라도 없으면 스킵) |

### 조회

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/customers` | 전체 고객 목록 |
| `POST` | `/api/customers` | 고객 신규 생성 (자동 채번) |
| `DELETE` | `/api/customers` | 벌크 삭제 (연관 결과 캐스케이드) |
| `GET` | `/api/customer/{id}` | 고객 분석 결과 (persona/nba/activities/qc + updated_at) |
| `GET` | `/api/sales-notes/{id}` | 고객별 영업 노트 |
| `POST` | `/api/sales-notes` | 영업 노트 추가 |
| `DELETE` | `/api/sales-notes` | 영업 노트 벌크 삭제 (연관 페르소나/NBA 유지) |
| `POST` | `/api/sales-notes/upload` | CSV 파싱 + 행별 검증 |
| `POST` | `/api/sales-notes/bulk-commit` | 유효 행만 DB insert |
| `POST` | `/api/sales-notes/check-dislikes` | DislikeCheckerAgent 실행 + 영속 |
| `GET` | `/api/all-sales-notes` | 전체 노트 통합 (날짜 내림차순) |
| `GET` | `/api/all-personas` | 전체 페르소나 + 고객 메타 |
| `GET` | `/api/all-nba` | 전체 NBA + 고객 메타 + 매칭 노트 RED FLAG 조인 |
| `GET` | `/api/all-activities` | 전체 Activity 플래튼 (기한 오름차순) |
| `GET` | `/api/all-qc` | 전체 QC (reviewed_at 내림차순) |

### AI Chat / 시스템

| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/chat` | CRM 챗 어시스턴트 (ChatAgent, 무상태) |
| `GET` | `/api/models` | 모델 레지스트리 + 현재 선택 |
| `POST` | `/api/model` | 분석 모델 변경 |
| `GET` | `/api/debug` | DB 상태 진단 (행 수, customers.json 존재) |
| `GET` | `/api/debug/env` | 환경 변수 진단 (DATABASE_URL 마스킹) |

### SSE 이벤트 형식

**개별 실행**:
```json
{"type": "log",   "text": "에이전트 실행 로그..."}
{"type": "done",  "completed_at": "2026-04-24 14:32:05"}
{"type": "error", "text": "오류 메시지..."}
```

**일괄 실행** (추가 이벤트):
```json
{"type": "progress", "index": 3, "total": 11, "customer_id": "C003",
 "company_name": "한국투자증권", "status": "started|done|skipped|error", "error": "..."}
{"type": "done", "total": 11, "succeeded": 8, "failed": 1, "skipped": 2,
 "completed_at": "2026-04-24 14:32:05"}
```

---

## 프로젝트 구조

```
agenticCRM/
├── data/
│   ├── customers.json        # 고객 기본 정보 (읽기 전용)
│   ├── sales_notes.json      # 영업 노트 초기 데이터 (DB 시드용)
│   └── action_plans.json     # 기존 액션 플랜 (읽기 전용)
├── src/
│   ├── agents/
│   │   ├── base_agent.py             # 공통 Agentic Loop (max_tokens 연속 생성, OpenRouter 가드)
│   │   ├── orchestrator.py           # 전체 파이프라인 조율 (tool_use)
│   │   ├── persona_agent.py          # Customer_Feedback 기반 선호도 분석
│   │   ├── nba_agent.py              # recency 가중치 NBA + 3단계 승인
│   │   ├── activity_agent.py         # NBA → Activity 변환 + 진행 상태 관리
│   │   ├── qc_agent.py               # 품질 검수 (부문별 점수 + verdict)
│   │   ├── dislike_checker_agent.py  # Action_Point ↔ explicit_dislikes 매칭
│   │   └── chat_agent.py             # CRM 챗 어시스턴트 (다중 읽기 도구)
│   ├── db/
│   │   └── database.py       # SQLAlchemy 모델 & 엔진 (SQLite/PostgreSQL 자동)
│   ├── tools/
│   │   ├── data_tools.py     # 데이터 액세스 레이어 (since_date 필터)
│   │   └── openrouter_client.py
│   └── main.py               # CLI 진입점
├── web/
│   ├── app.py                # FastAPI 앱 & API 라우트 (개별/일괄 SSE, 전체 조회, CRUD, Chat)
│   └── templates/
│       ├── index.html        # 대시보드 (7개 탭 + AI Chat 사이드바)
│       └── customer.html     # 고객 상세 (4개 섹션 카드)
├── output/                   # 분석 보고서 (.gitignore)
├── crm.db                    # SQLite DB (.gitignore)
├── sample_c002_dislike_action_points.csv  # 불만 탐지 테스트 픽스처
├── test_sales_notes_uploads.csv           # CSV 업로드 테스트 픽스처
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
| GPT-OSS 120B | OpenRouter (무료) | rate limit 있음 |
| GLM 4.5 Air | OpenRouter (무료) | rate limit 있음 |
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

**배포 후 진단**:
```
GET /api/debug      ← 테이블 행 수, customers.json 존재 여부
GET /api/debug/env  ← DATABASE_URL 마스킹, Railway 환경변수
```

---

## 기술 스택

- **Backend**: FastAPI, SQLAlchemy, Uvicorn
- **AI**: Anthropic Claude API (tool_use), OpenRouter
- **DB**: SQLite (로컬) / PostgreSQL (프로덕션, Railway)
- **Frontend**: Bootstrap 5.3, Bootstrap Icons, Vanilla JS (SSE, EventSource)
- **Config**: python-dotenv
