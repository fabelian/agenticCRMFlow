# NBA 3단계 승인 워크플로우 실행 계획

> 대상 프로젝트: `agenticCRM_flow`
> 최초 작성: 2026-04-25 (KST)
> 최종 개정: 2026-04-25 (KST) — v3 (AUTH 계획서와 책임 분리, Phase -1·결정 C 외부화)
> 범위: AI 제안(ai_proposed) → CRM 승인(crm_approved) → Sales 승인(sales_approved) 3단계 상태머신과, 각 Activity의 NBA 승인 상태 미러링을 "문서상 설계"에서 "실제로 동작하는 워크플로우"로 완성하기 위한 계획.
> **연계 문서**: 인증·사용자·역할·권한 시스템은 본 계획서가 아닌 [`AUTH_USER_MANAGEMENT_PLAN.md`](./AUTH_USER_MANAGEMENT_PLAN.md)에서 별도로 다룬다. 본 계획서의 결정 C, Phase -1의 인증 항목, `X-Actor` 헤더 가정 등은 모두 AUTH 계획서에 의해 **대체(supersede)**된다. AUTH 계획서가 먼저 실행 완료된 뒤 본 계획서의 Phase 0 이후가 시작된다.

---

## 0. 한눈에 보는 현재 상태

### 0-1. 이미 설계되어 있는 것 (프롬프트·UI 레벨)

| 레이어 | 상태 | 근거 |
|---|---|---|
| NBAAgent 프롬프트 | ✅ `approval: {status, ai_proposed_at, crm_approved_at/by, sales_approved_at/by}` 스키마 명시 | `src/agents/nba_agent.py` SYSTEM_PROMPT + `save_nba_recommendations` tool schema |
| ActivityAgent 프롬프트 | ✅ `nba_approval{linked_nba_rank, linked_nba_title, status, crm_approved_by, sales_approved_by}` + `activity_status{status, updated_at, updated_by, note}` 미러링 스키마 명시 | `src/agents/activity_agent.py` SYSTEM_PROMPT + `save_activity_schedule` tool schema |
| 프론트엔드 표시 | ✅ `.appr-ai / .appr-crm / .appr-sales` CSS, `apprLabel` 매핑, Activity 상세 모달에서 `linked_nba_rank`·`crm_approved_by`·`sales_approved_by` 렌더 | `web/templates/customer.html`, `web/templates/index.html` (NBA 승인 정렬·필터까지 구현) |

### 0-2. 완전히 빠져 있는 것 (데이터·API·상태전이)

| 레이어 | 현재 | 필요 |
|---|---|---|
| 저장된 NBA 데이터 | `immediate_actions / short_term_actions / medium_term_actions` 옛 스키마. `actions[]`도, `approval` 블록도 없음 | `actions[]` 단일 배열 + `approval` 블록 + 서버 생성 `action_id` |
| 저장된 Activity 데이터 | `status` 문자열 직접(`activity_status` 객체 X), `linked_nba_action` 제목 문자열(`linked_nba_rank` X), `nba_approval` 블록 없음 | 프롬프트 스키마와 일치시켜야 함. 프롬프트는 맞게 써 있어도 실제 저장된 파일은 옛 구조 |
| 상태전이 API | ❌ 전혀 없음 (`web/app.py`에 approve/reject/mutation 엔드포인트 자체 부재) | CRM 승인 / Sales 승인 / 회수(unapprove) / 근거 메모 / 감사로그 필요 |
| 인증·권한 | ❌ 전혀 없음 — 누구나 모든 엔드포인트 호출 가능 (CodeReview.md `#Fix 3`) | 승인 워크플로우의 전제 조건. 승인자가 누구인지 "누구나 POST할 수 있다"면 워크플로우 의미 없음 |
| 재실행 병합 | ❌ `save_nba` / `save_activities`가 매번 전체 덮어쓰기 (CodeReview.md `#Improve 4`) | CRM이 승인해둔 액션이 다음 배치 재실행에서 사라짐. **치명적**. 내용해시 기반 병합 필수 |
| 감사 로그 | ❌ 없음 | 누가 언제 어떤 근거로 승인/회수했는지 추적 불가. 금융권 내부통제 관점에서 필수 |

### 0-3. 이 프로젝트만의 제약 & 자산

- **KST 고정 오프셋**: `data_tools.now_kst_str()` 이미 존재 → 승인 타임스탬프 표기도 같은 유틸로 일관화 (UTC ISO8601 섞지 않기).
- **Activity envelope 패턴**: `{activities: [...], updated_at: ...}` + `_unwrap_activities` 호환 레이어 선례 있음 → NBA도 같은 패턴으로 확장 가능.
- **Red-flag 메타데이터 선례**: DislikeCheckerAgent가 노트에 `_red_flag / _red_flag_matched / _red_flag_reason / _red_flag_checked_at`를 박아두고 프론트·`/api/all-nba`가 재사용하는 패턴이 이미 성숙 → `approval`도 같은 철학으로 설계.
- **서버 사이드 조인 `/api/all-nba`**: 이미 한 번의 응답에 NBA·승인 필드·고객 메타를 합쳐 내려주는 패턴이 있음 → activity-to-NBA 승인 투영도 프론트가 아닌 서버에서 join.
- **ChatAgent 읽기 전용 tool_use**: 대시보드 사이드바에서 read-only 도구 호출 패턴 확립 → "CRM 미승인 몇 건?" 같은 쿼리를 자연스럽게 노출 가능.
- **CodeReview.md가 지목한 블로커** (`#Fix 1/2/3`, `#Improve 1/4`): 승인 워크플로우가 **실제로 의미 있으려면** 이 이슈들이 먼저 또는 동시에 해결되어야 함. 이 계획은 그 의존관계를 명시함.

---

## 1. 핵심 설계 결정

### 결정 A. 액션 식별자는 LLM이 아니라 서버가 부여한다 (stable_action_key + 정규화 + 유사도)

- **ID 포맷**: `NBA-{customer_id}-{seq:04d}` (예: `NBA-C002-0001`). 단조증가 시퀀스. 날짜는 ID에 박지 말고 `first_seen_at` / `last_seen_at` 메타필드로 따로 관리. 같은 날 두 번 재실행 시 seq 충돌 모호성 제거.
- 이유: LLM이 부여한 `rank`는 재실행 시 바뀐다. 승인 상태는 "내용"에 귀속되어야 하고, 추적은 "안정된 ID"로 해야 한다.

#### A-1. stable_action_key — LLM이 직접 부여하는 안정 슬러그

LLM은 같은 의도의 액션을 매번 다르게 표현(어순·구두점·"즉시"/"오늘 중" 변경)하므로 raw text의 sha1 만으로는 안정성이 부족하다. NBA 프롬프트에 다음을 추가:

> 각 action에 `stable_action_key`(snake_case 슬러그, 5~8 단어, 핵심 동사+목적어+대상으로 구성, 어휘 표현이 바뀌어도 동일 의도면 같은 키를 사용)를 포함하세요.
> 예: `apologize_call_to_lee_overdue_esg_items`, `quant_team_value_up_pilot_report`, `kif_in_person_meeting_2026h2_strategy`

#### A-2. 정규화 후 content_hash

서버가 다음 순서로 안정성을 강화한다:

1. `stable_action_key`가 있으면 1차 매칭 키로 사용.
2. 보조 검증용 `content_hash` = `sha1(normalize(title) + "|" + normalize(how_to) + "|" + deadline)[:12]`.
   - `normalize()` = NFC → 소문자 → 연속공백 1칸 → 한국어 특수문자(·,「」"") 제거 → 양끝 trim.
3. 매칭 우선순위: ① `stable_action_key` 일치 → 동일 액션 ② key 미일치이지만 `content_hash` 일치 → 동일 액션 ③ key·hash 둘 다 미일치이지만 **embedding 코사인 유사도 ≥ 0.92** (Phase 1에 OpenAI `text-embedding-3-small` 또는 동급 모델로 도입) → 동일 액션 후보로 제안 (UI에 "이 액션은 기존 NBA-C002-0003의 변형으로 추정됩니다 — [같은 액션으로 보기] / [새 액션으로 보기]" 토글) ④ 모두 미일치 → 신규.
4. 임베딩 호출 비용 절감: 활성 actions만 캐시(`actions_meta` JSON 컬럼에 `embedding`, `embedding_model`, `embedded_at` 저장). 캐시 hit이면 재호출 안 함.

이로써 회귀 #5("동일 입력 재실행 → 승인 유지")가 LLM 표현 변화에 둔감해진다.

### 결정 B. NBA가 원본, Activity는 투영(projection)

- `activity.nba_approval.status`는 **Activity가 스스로 바꾸지 않는다**. NBA 승인 상태가 변경되면 서버가 그 시점의 Activity 레코드들에 재투영한다.
- 동기화 트리거 지점:
  1. CRM/Sales 승인 API 호출 시 — 해당 `action_id`를 참조하는 모든 activity의 `nba_approval.status`/`_approval_projected_at` 갱신.
  2. NBA 재실행 시 — `save_nba`의 merge 이후, `save_activities`가 참조하는 `action_id` 기준으로 일괄 재투영.
- Activity에는 `_approval_projected_at` 메타필드(KST timestamp)를 추가해 "언제 NBA로부터 투영됐는지" 추적.

### 결정 C. 승인자 식별 — AUTH 계획서로 외부화 (v3에서 개정)

> ⚠️ **v3 변경**: 본 결정의 actor 식별 메커니즘(`X-Actor` 헤더 → `users.json` 화이트리스트 → SSO)은 모두 [`AUTH_USER_MANAGEMENT_PLAN.md`](./AUTH_USER_MANAGEMENT_PLAN.md)로 이전되었다. 본 계획서는 AUTH 계획서가 제공하는 **`current_user` 의존성**과 **`require_role(*needed)` 가드**를 전제로 작성된다.

- **Actor 출처**: 본 계획의 모든 mutation 엔드포인트(`/api/nba/.../crm-approve` 등)는 FastAPI `Depends(require_role("crm"))` / `Depends(require_role("sales"))`를 부착한다. `current_user.id`(예: `sungwoo`, `yj_kim`)가 `approval.crm_approved_by` / `sales_approved_by`로 기록된다.
- **`X-Actor` 헤더 폐기**: v1·v2에서 가정했던 `X-Actor` 헤더 방식은 **사용하지 않는다**. 클라이언트는 세션 쿠키(`crm_session`) 또는 API key(`Authorization: Bearer crm_xxx`)로 인증하고, 서버는 그것으로부터 actor를 도출한다.
- **role 검증**: AUTH 계획서가 정의하는 4-tier 역할(`admin`, `crm`, `sales`, `viewer`) 중 `crm` / `sales`만 본 워크플로우의 mutation에 진입 가능. `viewer`는 `GET /api/approvals/pending` 같은 read-only 경로만 허용. `admin`은 모든 경로에 대해 통과(감사 목적의 `event_type="admin_override"` 자동 기록).
- **점진적 enforcement**: AUTH 계획서의 `AUTH_ENFORCEMENT` 환경변수(`off → soft → strict`, 결정 E in AUTH plan)에 본 계획의 모든 엔드포인트가 동참한다. `soft` 단계(2주)에서는 미인증 호출도 통과하되 `audit_violation` 이벤트가 `auth_events`에 기록된다.

#### C-1. Maker-Checker (직무 분리) 정책 — 금융권 내부통제 핵심

같은 actor가 CRM 승인과 Sales 승인을 둘 다 할 수 없도록 서버에서 강제한다. 작은 조직에서 한 사람이 두 역할(`roles: ["crm", "sales"]`)을 모두 보유할 수 있더라도, **한 액션에 대해서는 두 단계를 같은 사람이 수행하지 못한다**.

서버 검증 규칙(`POST /api/nba/{cid}/{action_id}/sales-approve` 진입 시):

```python
if action["approval"]["crm_approved_by"] == current_user.id:
    raise HTTPException(422, detail={
        "error": "segregation_of_duties",
        "message": "동일인이 CRM·Sales 단계를 모두 승인할 수 없습니다.",
        "crm_approved_by": action["approval"]["crm_approved_by"],
        "sales_attempted_by": current_user.id,
    })
```

설정 플래그 `APPROVAL_ALLOW_SELF_CHECK=true`(기본 false)로 1인 운영 모드에서만 우회 허용 — 환경변수로만 토글 가능, UI 노출 X. 이 플래그는 감사로그에 매 우회마다 `event_type="self_check_bypass"`로 기록.

회귀 체크리스트 #13 추가: "동일인이 CRM 승인 후 Sales 승인 시도 → 422 (segregation_of_duties)".

### 결정 D. 재실행 시 승인 상태 보존

- `save_nba`를 **shallow merge**로 전환 (CodeReview.md `#Improve 4` 확대 적용).
- 새 NBA 결과가 들어오면: 기존 `actions[]`를 `content_hash`로 인덱싱 → 동일 hash면 `approval` 블록 승계 → 없어진 액션은 `approval.status = "superseded"`로 마킹(실제 삭제 X, append-only).
- `save_activities`도 같은 원리로 기존 `activity_status` 보존 (진행 중인 일을 LLM 재실행이 "pending"으로 되돌리면 안 됨).

### 결정 E. 모든 상태 변경은 감사 로그에 기록

- 새 테이블 `approval_events` (append-only):
  - `id`, `customer_id`, `action_id`, `event_type`(ai_proposed / crm_approved / crm_revoked / sales_approved / sales_revoked / superseded / migrated_from_legacy / self_check_bypass), `actor`, `note`, `created_at` (KST).
- UI와 API는 현재 상태만 보여주더라도, 내부적으로는 이 테이블이 모든 전이의 진실.
- **마이그레이션 합성 이벤트**: 4-1 마이그레이션 시 액션 당 1건씩 `event_type="migrated_from_legacy"`, `actor="system"`, `note="legacy bucket schema → actions[]"` 이벤트를 기록한다. UI의 "승인 이력" 위젯이 빈 배열로 렌더링되는 문제를 방지하고, 감사 관점에서도 "이 액션은 어느 시점에 시스템에 들어왔는가"의 출발점이 명확해진다.

### 결정 F. action_plans.json (`AP-CXXX-NNN`) 과 NBA action_id 의 관계

이 프로젝트는 `data/action_plans.json`에 `AP-C001-001` 식 식별자가 이미 존재하고, NBA 결과 본문에서도 `"AP-C002-001 미완료 건"` 같은 자연어 참조가 빈번하다. 두 ID 체계의 관계를 다음과 같이 정의한다:

- **AP-* (Action Plan)** = 영업 담당자가 사전에 수립해 둔 **고정된 영업 계획**. 수동 입력. 완료/진행중/실패 상태를 영업자가 갱신.
- **NBA-* (Next Best Action)** = LLM이 매 분석 시점에 생성하는 **동적 추천**. AI 제안 → CRM/Sales 승인 워크플로우의 단위.
- 관계: NBA action은 0개 이상의 AP를 참조할 수 있다. NBA → AP는 다대다(M:N). AP → NBA는 보고용 역참조.
- NBA action 스키마에 `derived_from_plans: ["AP-C002-001", ...]` 필드 추가 (배열, 비어 있을 수 있음).
- 마이그레이션 4-1에서 옛 NBA의 `rationale`/`how_to`에 `AP-CXXX-NNN` 패턴이 등장하면 정규식으로 추출해 `derived_from_plans`에 채운다(LLM 재호출 없이 결정).
- NBA 프롬프트에 `derived_from_plans`를 명시적으로 출력하도록 추가: "참조한 action_plan이 있다면 plan_id 배열로 명시. 없으면 빈 배열".
- `/api/customer/{cid}` 응답에서 NBA action ↔ AP 양방향 링크 제공.

이 결정으로 "이 NBA 추천은 어느 영업 계획에서 비롯됐나?"를 추적 가능해지고, 마이그레이션 시 AP-* 식별자가 자연어 본문에 박혀 있던 데이터의 정합성이 살아난다.

### 결정 G. 동시성 잠금 단위 — Phase 1: 거친 락 + 자동 재시도, Phase 3: 액션별 row 분리

NBAResult는 한 customer = 한 row, 그 row의 JSON 안에 `actions[]` 다수가 들어 있는 구조. 잠금 단위가 거칠다.

**Trade-off 분석**:

| 옵션 | 장점 | 단점 |
|---|---|---|
| (a) 그대로 + `with_for_update()` | 코드 변경 최소. 정합성 안전. | 같은 고객의 다른 action 동시 승인 시 한 명은 항상 409 → UX 저하 |
| (b) `nba_actions` 테이블 분리 (한 row = 한 action) | 액션별 row-level lock. 동시성 깔끔. | 스키마 마이그레이션 큼. envelope 패턴 깨짐. 기존 `/api/all-nba` 조인 로직 재작성 필요 |
| (c) Postgres JSONB 부분 업데이트 + per-action `version` | row 잠금 없이 원자적 부분 수정. 스키마 안 깨짐. | SQLite 미지원 → 로컬/Railway 분기 필요. 코드 복잡도↑ |

**결정**:
- **Phase 1~2**: 옵션 (a). 단 클라이언트가 409 받으면 즉시 GET으로 최신 `version` 재조회 후 1회 자동 재시도(투명 재시도). UI에는 토스트 없이 처리. 이 트래픽 패턴은 보통 reviewer가 동시에 같은 고객을 보지 않아 충돌이 드물 것으로 가정.
- **Phase 3 (선택)**: 운영 데이터 기준 409 빈도가 분당 1건 이상으로 측정되면 옵션 (b)로 마이그레이션. 그 전까지는 (a)+자동재시도가 충분.
- 옵션 (c)는 SQLite/Postgres 분기 비용이 본 워크플로우 외 부분에도 영향이 커서 보류.

회귀 체크리스트 #8을 다음과 같이 보강: "동시 승인 → 한 건 즉시 성공, 다른 건 409 후 자동 재시도 1회 → 결국 둘 다 성공(다른 액션이라면) 또는 정책상 두 번째는 422(같은 액션이라면)".

### 결정 H. `avoid_actions` 의 승인 스코프

NBA에는 `actions[]` 외에 `avoid_actions[]`("절대 하지 말 것")가 있다. 정책:

- `avoid_actions`는 **개별 승인 단위가 아니다**. 즉 별도 `approval` 블록을 가지지 않는다.
- 대신 **NBA 결과 전체의 메타 승인** 개념으로 다룬다: 한 고객의 NBA 분석 결과 자체에 `nba_summary_approval` 블록을 두고, 여기서 CRM/Sales가 "회피사항 인지 + 분석 방향성 동의"를 sign-off.
- `nba_summary_approval` 스키마(NBA `data` JSON 최상위):
  ```json
  "nba_summary_approval": {
    "status": "ai_proposed | crm_acknowledged | sales_acknowledged",
    "ai_proposed_at": "...",
    "crm_acknowledged_at": null,
    "crm_acknowledged_by": null,
    "sales_acknowledged_at": null,
    "sales_acknowledged_by": null,
    "version": 1
  }
  ```
- 이 블록은 개별 action 승인의 **전제 조건**: 어떤 action도 `nba_summary_approval`이 `crm_acknowledged` 이상이 아니면 CRM 승인 불가(422). UI에서 "분석 결과를 먼저 확인해주세요" 안내.
- API: `POST /api/nba/{cid}/summary/crm-acknowledge` / `sales-acknowledge`. revoke 동일.

이 결정으로 "회피사항을 안 보고 액션만 승인"하는 우회를 막는다.

### 결정 I. Sales 승인 ↔ Activity 자동 시작 (옵션 → Phase 1로 승격)

기존 계획은 "Sales 승인 완료 시 Activity 자동 in_progress 전환"을 Phase 4 옵션으로 미뤘으나, 미루면 Sales 승인 후 누군가 Activity status를 별도로 토글해야 하는 이중 작업이 생긴다. 워크플로우의 자연스러운 일부로 Phase 1로 승격한다.

- 정책: Sales 승인이 발생하면, 해당 action의 모든 연결 activity 중 `activity_status.status == "pending"`인 것들을 `in_progress`로 전이한다 (`updated_by = "system:sales_approved"`, `note = "Auto-transition on sales approval"`).
- `in_progress`/`completed`/`cancelled`는 그대로 유지(이미 진행 중인 것을 덮지 않음).
- 설정 플래그 `AUTO_START_ACTIVITY_ON_SALES_APPROVE`(기본 true)로 비활성화 가능 — 보수적 운영 환경 대응.
- Sales revoke 시에는 자동 되돌림 안 함 — 이미 시작된 일을 시스템이 임의로 멈추는 것은 위험. 운영자가 수동 결정.

회귀 체크리스트 #14 추가: "Sales 승인 시 연결 activity의 pending → in_progress, in_progress 항목은 변경 없음".

---

## 2. 상태 머신

```
                ┌──────────────┐
   [NBA 생성] → │ ai_proposed  │
                └──────┬───────┘
                       │ POST /api/nba/{cid}/{action_id}/crm-approve  (actor=CRM)
                       ▼
                ┌──────────────┐
                │ crm_approved │ ◄──── POST /api/nba/{cid}/{action_id}/crm-revoke
                └──────┬───────┘        (→ ai_proposed 로 복귀)
                       │ POST /api/nba/{cid}/{action_id}/sales-approve  (actor=Sales)
                       ▼
                ┌──────────────┐
                │sales_approved│ ◄──── POST /api/nba/{cid}/{action_id}/sales-revoke
                └──────────────┘        (→ crm_approved 로 복귀)
```

추가 상태:
- `superseded`: NBA 재실행으로 더이상 제안되지 않는 액션. UI에서 "과거 이력" 탭으로만 노출.
- `rejected` (선택): CRM이 명시적으로 "이 제안은 하지 말라" 기록. 기본 플로우에는 포함하지 않음.

**전이 규칙**:
- `sales_approved`는 반드시 `crm_approved`를 거친다 (skip 금지).
- `sales-revoke`는 `sales_approved → crm_approved`만 허용.
- `crm-revoke`는 `crm_approved → ai_proposed`만 허용 (이미 sales까지 승인된 건은 먼저 sales-revoke 필요).
- 재실행으로 내용이 바뀌면(`content_hash` 미스) 이전 action은 `superseded`로 freeze, 새 action은 `ai_proposed`로 시작.

---

## 3. 스키마

### 3-1. NBA (DB: `nba_results.data`)

```jsonc
{
  "summary": "...",
  "analysis_date": "2026-04-25",
  "risk_level": "high",
  "reference_notes": [{ "note_id": "...", "activity_date": "...", "action_point": "...", "recency_weight": 0.92 }],
  "top_priority_comparison": { ... },

  "nba_summary_approval": {                          // 결정 H — 분석 결과 전체 sign-off
    "status": "ai_proposed",                         // ai_proposed | crm_acknowledged | sales_acknowledged
    "ai_proposed_at": "2026-04-25 09:12",
    "crm_acknowledged_at": null,
    "crm_acknowledged_by": null,
    "sales_acknowledged_at": null,
    "sales_acknowledged_by": null,
    "version": 1
  },

  "actions": [
    {
      "action_id": "NBA-C002-0001",                  // 서버 생성 (단조증가 시퀀스)
      "stable_action_key": "apologize_call_to_lee_overdue_esg_items",  // LLM 생성 (결정 A-1)
      "content_hash": "a1b2c3d4e5f6",                // 서버 정규화 후 hash (결정 A-2)
      "first_seen_at": "2026-04-20 09:12",           // 최초 등장
      "last_seen_at": "2026-04-25 09:12",            // 가장 최근 NBA 실행에서 재등장
      "derived_from_plans": ["AP-C002-002"],         // 결정 F — action_plans 역참조
      "rank": 1,
      "title": "...",
      "rationale": "...",
      "how_to": "...",
      "deadline": "2026-04-28",
      "related_note_ids": ["SN-C002-..."],
      "expected_reaction": "...",
      "success_metric": "...",
      "approval": {
        "status": "ai_proposed",                     // ai_proposed | crm_approved | sales_approved | superseded
        "ai_proposed_at": "2026-04-25 09:12",        // now_kst_str()
        "crm_approved_at": null,
        "crm_approved_by": null,
        "crm_approved_note": null,
        "sales_approved_at": null,
        "sales_approved_by": null,
        "sales_approved_note": null,
        "version": 1                                 // 낙관적 락
      }
    }
  ],

  "actions_meta": {                                  // 결정 A-2 — 임베딩 캐시 (서버 전용)
    "NBA-C002-0001": {
      "embedding": [0.0123, -0.0456, ...],
      "embedding_model": "text-embedding-3-small",
      "embedded_at": "2026-04-25 09:12"
    }
  },

  "avoid_actions": [ ... ],                          // 개별 승인 X (결정 H), nba_summary_approval로 일괄 sign-off
  "expected_outcomes": "...",
  "updated_at": "2026-04-25 09:12"                   // save_nba가 주입
}
```

### 3-2. Activity (DB: `activities.data` — envelope 유지)

```jsonc
{
  "activities": [
    {
      "id": "ACT-C002-001",
      "title": "...",
      "type": "email",
      "due_date": "2026-04-28",
      "priority": "high",
      "assigned_to": "김영민",
      "description": "...",
      "checklist": [ ... ],
      "depends_on": null,
      "expected_outcome": "...",

      "activity_status": {                           // 활동 자체 진행 상태
        "status": "pending",                         // pending | in_progress | completed | cancelled
        "updated_at": null,
        "updated_by": null,
        "note": null
      },

      "nba_approval": {                              // NBA로부터 투영 (서버가 갱신)
        "linked_action_id": "NBA-C002-20260425-001", // 이게 진짜 연결키
        "linked_nba_rank": 1,                        // 참고용 (재실행 시 바뀔 수 있음)
        "linked_nba_title": "...",
        "status": "ai_proposed",
        "crm_approved_by": null,
        "sales_approved_by": null,
        "_approval_projected_at": "2026-04-25 09:13" // 언제 투영됐는지
      }
    }
  ],
  "updated_at": "2026-04-25 09:13"
}
```

### 3-3. 신규 테이블 `approval_events` (append-only)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | INT PK | 자동증가 |
| customer_id | VARCHAR | |
| action_id | VARCHAR | `NBA-...` |
| event_type | VARCHAR | `ai_proposed` / `crm_approved` / `crm_revoked` / `sales_approved` / `sales_revoked` / `superseded` |
| actor | VARCHAR | `current_user.id` (AUTH 계획서가 제공). `admin` 역할의 우회는 `actor=<admin_id>` + `event_type` 끝에 `:admin_override` 접미사 |
| note | TEXT | 승인/회수 근거 메모 |
| prev_status | VARCHAR | 전이 직전 상태 |
| new_status | VARCHAR | 전이 직후 상태 |
| created_at | TIMESTAMP | KST |

---

## 4. 마이그레이션

### 4-1. 기존 NBA JSON (`immediate_actions/short_term/medium_term`) → 새 스키마

`scripts/migrate_nba_schema.py`:

1. DB의 `nba_results` 전 레코드 순회.
2. 옛 3-버킷 형식 감지 → `immediate + short_term + medium_term` 순서로 평탄화, 각 항목에 `rank = idx+1`, `action_id = NBA-{cid}-{updated_at:YYYYMMDD}-{idx+1:03d}` 부여.
3. `approval.status = "ai_proposed"`, `ai_proposed_at = updated_at`로 초기화 (우리가 과거 승인 이력을 모르므로 모두 ai_proposed로 시작하는 것이 안전).
4. `deadline`이 "즉시/2주 내" 같은 문자열이면 `updated_at` 기준으로 상대 오프셋을 계산해 `YYYY-MM-DD`로 재작성 (LLM 재호출 없이 규칙 기반 변환).
5. 원본은 `data/_legacy/nba_results_{YYYYMMDD}.json`로 백업.

### 4-2. 기존 Activity → envelope + mirror 필드 채우기

`scripts/migrate_activity_schema.py`:

1. `_unwrap_activities` 레이어 유지 → 호환성 확보됨.
2. 각 activity의 `status` 문자열 → `activity_status.status`로 이주(`updated_at/by/note`는 null).
3. `linked_nba_action` 제목 문자열을 키로 마이그레이션된 NBA actions에서 제목 매칭 → `nba_approval.linked_action_id` 주입. 매칭 실패 시 `linked_action_id: null` + `_approval_projected_at: null` (수동 점검 대상).
4. 감사 로그: 마이그레이션 이벤트 자체는 `approval_events`에 **기록하지 않는다**(과거를 조작한 것처럼 보이므로). 대신 별도 `data_tools.log_migration(...)`으로 기록.

### 4-3. `approval_events` 테이블 생성

- `src/db/database.py`에 `ApprovalEvent` 모델 추가 → `Base.metadata.create_all(engine)`가 자동 생성.
- PostgreSQL(Railway)에서는 최초 기동 시 자동 생성. 기존 데이터 무관.

---

## 5. API 설계

모든 승인 mutation 엔드포인트는 공통 요구사항:
- **인증**: `Depends(current_user)` 필수. 미인증 호출은 `AUTH_ENFORCEMENT=strict`에서 401, `soft`에서 통과 + `audit_violation` 기록 (AUTH 계획서 결정 E).
- **인가**: `Depends(require_role("crm"))` 또는 `Depends(require_role("sales"))`. role 불일치 시 403.
- **CSRF**: 쿠키 세션 사용 시 `X-CSRF-Token` 헤더 필수 (AUTH 계획서). API key 사용 시 면제.
- 바디: `{ "note": "근거 메모", "expected_version": 1 }` (낙관적 락).
- 응답: 갱신된 전체 `approval` 객체 + 전이된 이벤트 ID.
- 실패 시: 409(버전 불일치), 422(잘못된 전이 / `segregation_of_duties`), 403(role 불일치), 401(미인증, strict 단계).

| Method | Path | 설명 | 관련 이벤트 |
|---|---|---|---|
| GET | `/api/nba/{cid}` | 현재 상태 (변경 없음, 기존 `/api/customer/{cid}`에 포함됨) | — |
| POST | `/api/nba/{cid}/summary/crm-acknowledge` | nba 분석결과 전체 CRM sign-off (결정 H) | nba_summary_crm_acknowledged |
| POST | `/api/nba/{cid}/summary/sales-acknowledge` | sales sign-off | nba_summary_sales_acknowledged |
| POST | `/api/nba/{cid}/{action_id}/crm-approve` | `ai_proposed → crm_approved` (전제: nba_summary가 crm_acknowledged 이상) | crm_approved |
| POST | `/api/nba/{cid}/{action_id}/crm-revoke` | `crm_approved → ai_proposed` | crm_revoked |
| POST | `/api/nba/{cid}/{action_id}/sales-approve` | `crm_approved → sales_approved` (전제: 동일인 self-check 차단 — 결정 C-1) | sales_approved |
| POST | `/api/nba/{cid}/{action_id}/sales-revoke` | `sales_approved → crm_approved` | sales_revoked |
| POST | `/api/nba/{cid}/{action_id}/promote-variant` | 변형 제안(`variant_proposals[]`)을 별도 신규 action으로 분리 | ai_proposed |
| GET | `/api/nba/{cid}/{action_id}/events` | 해당 액션의 approval_events 시계열 | — |
| GET | `/api/approvals/pending` | 승인 대기 큐. 쿼리: `role=crm\|sales`, `limit=50` (max 200), `offset=0`, `sort=due_date\|risk_level\|first_seen_at` (default `due_date`), `dir=asc\|desc`, `customer_id=`, `risk_level=high,medium`, `red_flag_only=true`. 응답: `{items: [...], total: N, has_more: bool}` | — |
| POST | `/api/approvals/bulk` | 여러 action_id 일괄 전이. body: `{role, action: "crm-approve\|crm-revoke\|sales-approve\|sales-revoke", items: [{cid, action_id, expected_version, note}], mode: "all_or_nothing\|per_item"}`. **트랜잭션 정책은 body의 `mode`로 결정** (S3 — 기본 `per_item`). 응답: `{succeeded: [...event_ids], failed: [{cid, action_id, code, message}]}`. 부분 실패 시 HTTP 207. | crm_approved/sales_approved × N |
| POST | `/api/approvals/bulk-revoke-by-events` | 직전 bulk 응답의 `event_ids`를 받아 일괄 revoke (실수 복구) | crm_revoked/sales_revoked × N |

### 5-1. 서버 측 투영 로직

`src/tools/approval_tools.py` (신규 모듈):

```
def transition(customer_id, action_id, event_type, actor, note, expected_version):
    with db_session() as s:
        nba = s.query(NBAResult).filter_by(customer_id=customer_id).with_for_update().one()
        action = _find_by_id(nba.data["actions"], action_id)
        _assert_valid_transition(action["approval"]["status"], event_type)
        _assert_version(action["approval"]["version"], expected_version)

        prev = action["approval"]["status"]
        now = now_kst_str()
        # 1) NBA action 갱신
        _apply_transition(action, event_type, actor, note, now)
        action["approval"]["version"] += 1
        s.merge(nba)

        # 2) approval_events 기록
        s.add(ApprovalEvent(customer_id, action_id, event_type, actor, note, prev, action["approval"]["status"], now))

        # 3) Activity 투영
        acts = s.query(ActivitySchedule).filter_by(customer_id=customer_id).one_or_none()
        if acts:
            changed = _reproject_activities(acts.data, action_id, action["approval"])
            if changed:
                acts.data["updated_at"] = now
                s.merge(acts)
        s.commit()
```

핵심은 `with_for_update()`로 row-level lock + `expected_version` 체크로 double-approve 방지.

#### 5-1-1. Bulk 트랜잭션 모드 (S3)

`POST /api/approvals/bulk`의 `mode` 파라미터:

- **`per_item`** (기본): 각 아이템을 독립 트랜잭션으로 처리. 한 건이 실패해도 다른 건은 커밋. 응답은 HTTP 207 (Multi-Status), body에 `succeeded[]` / `failed[]` 분리. UI는 실패 건만 빨갛게 다시 표시 + 재시도 버튼.
- **`all_or_nothing`**: 모두 같은 DB 트랜잭션 안에서 시도. 한 건이라도 실패하면 전체 롤백, HTTP 422. 회계·정산 같이 부분 성공이 의미 없을 때 사용.
- 기본을 `per_item`으로 잡는 이유: 승인 워크플로우는 reviewer가 사람이라 부분 성공 후 실패 건만 follow-up 하는 것이 자연스럽다.

### 5-2. ChatAgent 확장

`get_pending_approvals(role)` 읽기 전용 tool을 ChatAgent TOOLS에 추가. 프롬프트에 "CRM 미승인 몇 건? 등의 질문에 이 도구를 사용"을 명시. 이는 기존 read-only tool_use 패턴의 자연스러운 확장이며 코드 변경 범위가 작다.

---

## 6. 에이전트 쪽 변경

### 6-1. NBAAgent

- **프롬프트**: 현재는 이미 `approval` 블록을 요구하므로 골격은 유지. 다음 변경:
  - `action_id`는 LLM이 **만들지 말라** — "서버가 부여합니다. 응답에 포함하지 마세요".
  - `stable_action_key` (snake_case 슬러그, 5~8 단어, 핵심 동사+목적어+대상)를 모든 action에 **반드시** 포함. 좋은/나쁜 예시 다수 제공.
  - `derived_from_plans` 배열을 모든 action에 포함 (참조한 `AP-CXXX-NNN` 식별자, 없으면 빈 배열).
  - System message에 직전 NBA 결과의 활성 actions의 `{action_id, stable_action_key, title}` 목록을 주입 → "동일 의도가 있으면 같은 key 재사용" 명시.
  - `nba_summary_approval`은 LLM이 출력하지 말고 서버가 초기화.
- **`save_nba_recommendations` 실행 시 서버 측 책임** (`src/tools/action_identity.py` + `data_tools.save_nba`):
  1. 기존 NBA 로드 → 활성 actions를 `(stable_action_key, content_hash, embedding)` 3-튜플로 인덱싱.
  2. 새 `actions[]` 각 항목에 대해:
     - `content_hash = sha1(normalize(title) + "|" + normalize(how_to) + "|" + deadline)[:12]` 계산.
     - 매칭 우선순위: ① stable_action_key 일치 ② content_hash 일치 ③ 임베딩 코사인 유사도 ≥ 0.92.
     - 매칭 시: 기존 `action_id`·`approval`·`version`·`first_seen_at` 승계, `last_seen_at` = `now_kst_str()`. 임베딩 매칭 단계에 도달했고 기존 액션이 sales_approved면 → `variant_proposals[]`에 attach (별도 카드 분리 X, 결정 H 엣지케이스).
     - 미매칭: 새 `action_id = NBA-{cid}-{next_seq:04d}` 부여 + `approval = {status:"ai_proposed", ...}` + `first_seen_at = last_seen_at = now_kst_str()` + 임베딩 계산하여 `actions_meta[action_id]`에 캐시.
  3. 이번 응답에 미포함된 기존 active action: `approval.status="superseded"`로 마킹하여 `actions[]`에 유지(append-only). 단 sales_approved이고 `force=false`면 freeze 유지.
  4. `approval_events`에 신규 ai_proposed / superseded 이벤트 append.
  5. NBA 신규 row면 `nba_summary_approval = {status:"ai_proposed", ai_proposed_at: now, version:1, ...}` 초기화. 기존 row면 그대로 유지(재실행이 sign-off를 무효화하지 않음).
- 이 전환을 위해 `data_tools.save_nba`를 `save_nba_with_merge`로 리팩터링 (기존 시그니처 유지, 내부만 merge).

### 6-2. ActivityAgent

- **프롬프트**: `nba_approval.linked_action_id`를 반드시 포함하라고 추가. `linked_nba_rank`는 참고용임을 명시. `crm_approved_by/sales_approved_by/status`는 LLM이 채우지 말고 모두 `null`/기본값으로 두라고 명시 (서버가 투영). `activity_status`는 신규 activity일 때만 LLM이 `pending`으로 초기화.
- **서버 측**: `save_activities`는 envelope 유지하면서 각 activity의 `nba_approval`에 대해 서버가 현재 NBA 스냅샷으로 재투영 (`status` + `crm_approved_by` + `sales_approved_by` + `_approval_projected_at = now_kst_str()`). `activity_status`는 merge — 기존 activity가 `pending`이 아닌 경우 LLM 응답의 `activity_status`는 무시하고 기존값 보존.

### 6-3. QCAgent

- 신규 검증 항목 추가:
  - 모든 activity의 `nba_approval.linked_action_id`가 현재 NBA의 활성 `actions[]`에 존재하는가.
  - `approval.status`가 유효 열거값인가.
  - `sales_approved_at`이 `crm_approved_at`보다 이르지 않은가.
- fail 시 재실행 대상은 ActivityAgent (Orchestrator 기존 재시도 경로 그대로 사용).

### 6-4. DislikeCheckerAgent

- 이번 워크플로우에서는 기능 변경 없음. 단, CRM 승인 UI에서 "red-flag 매칭된 NBA는 기본적으로 경고 배너 표시"로 UX 연동 (CRM이 실수로 dislike 위반 액션을 승인하지 않도록).

---

## 7. UI 변경

### 7-1. 고객 상세 (`web/templates/customer.html`)

- **분석 결과 sign-off 카드** (NBA 분석 요약 위쪽): `nba_summary_approval` 상태를 표시하고 `[분석 결과 인지 (CRM)]` / `[분석 결과 인지 (Sales)]` 버튼 노출. 메모는 선택. 이 단계가 끝나야 개별 action 승인 버튼이 활성화됨(결정 H).
- 각 NBA action 카드에 다음 노출:
  - `[CRM 승인]` / `[CRM 승인 회수]` (role=crm & 조건 맞을 때, summary가 crm_acknowledged 이상일 때만 활성)
  - `[Sales 승인]` / `[Sales 승인 회수]` (role=sales & status=crm_approved 일 때, 동일인 self-check면 비활성 + 툴팁 안내)
  - `derived_from_plans`가 있으면 `참조 플랜: AP-C002-002 ↗` 칩 (클릭 시 action_plans 상세).
  - `variant_proposals[]`가 있으면 `[변형 제안 N건 보기]` 토글 — 펼치면 LLM이 새로 표현 변경한 안과 그 임베딩 유사도 표시. CRM이 `[신규 액션으로 분리]` 클릭 시 `POST .../promote-variant` 호출.
- 버튼 클릭 시 근거 메모 입력 모달 → POST → 성공 시 해당 카드만 부분 갱신.
- `approval_events` 시계열을 "승인 이력" 아코디언으로 노출 (마이그레이션된 액션은 첫 줄에 `migrated_from_legacy` 표시).
- Sales 승인으로 자동 시작된 activity는 `🟡 자동 시작 (sales 승인)` 인디케이터 표시.
- 관련 Activity 카드들은 `linked_action_id`로 연결되어 있으므로 같은 승인 색상 뱃지가 자동으로 바뀌어야 함(서버가 투영하므로 클라이언트는 새로고침만 하면 됨).

### 7-2. 대시보드 (`web/templates/index.html`)

- **승인 대기 큐 위젯**: 상단에 "Summary 인지 대기 K건 / CRM 승인 대기 N건 / Sales 승인 대기 M건" 카드 추가. 클릭 시 `/api/approvals/pending?role=xxx`로 필터된 목록(페이징·정렬·필터 포함).
- **일괄 승인**: 체크박스 선택 + `[선택 CRM 승인]` → `POST /api/approvals/bulk` (기본 `mode=per_item`). 결과 패널에 success/failed 분리 표시 + 실패 건만 재시도 가능.
- **방금 승인 일괄 회수**: 직전 bulk 응답을 기억해 `[방금 승인 회수]` 버튼 제공 → `POST /api/approvals/bulk-revoke-by-events`.
- 기존 "NBA 승인" 정렬·필터는 그대로 유지.

### 7-3. CodeReview.md `#Fix 1` 선행 필수

- `escHtml`이 `"`와 `'`를 이스케이프하지 않는 상태에서 승인 근거 메모를 렌더링하면 XSS 위험이 직접 증가. 승인 버튼 + 메모 UI 구현 전에 `escHtml`을 완전한 HTML-엔티티 치환으로 고쳐야 함.

---

## 8. 엣지 케이스 & 정책

| 케이스 | 정책 |
|---|---|
| NBA 재실행으로 `crm_approved` 상태인 action의 내용이 바뀜 | 기존 action은 `superseded`, 새 action은 `ai_proposed`. CRM에게 "이전 승인건이 대체됐다" 알림 배너. |
| Sales가 승인한 상태에서 NBA 재실행, **stable_action_key 일치** | 동일 액션으로 인식 — 기존 `approval`/`version`/`action_id` 그대로 승계. `last_seen_at`만 갱신. 새 카드 생성 X. |
| Sales가 승인한 상태에서 NBA 재실행, **key 미일치 + 임베딩 유사도 ≥ 0.92** | "변형 제안"으로 분류 — 기존 sales_approved action에 `variant_proposals[]` 배열로 attach (별도 카드 분리 X). UI에 "이 승인된 액션의 변형 제안 1건이 있습니다 — [보기]" 토글. CRM이 명시적으로 "신규 액션으로 분리"를 선택하기 전까지 별도 카드로 노출되지 않음. |
| Sales가 승인한 상태에서 NBA 재실행, **유사도 < 0.92** | 기존 sales_approved action은 `force=false`(기본)에서 freeze 유지. 새 action은 `ai_proposed`로 추가. 운영자가 두 액션을 모두 보고 판단. |
| CRM이 실수 승인 | `crm-revoke`로 되돌리기 + `note`에 사유 필수. revoke도 이벤트로 기록. |
| Activity가 이미 `completed`인데 NBA action이 `superseded`로 바뀜 | Activity의 `activity_status`는 그대로 유지(과거 실행은 사실). `nba_approval.status`만 `superseded`로 투영. UI에서 "이미 실행 완료" 뱃지로 구분. |
| 낙관적 락 충돌 (다른 액션) | 409 반환. 클라이언트가 GET으로 최신 `version` 재조회 후 1회 자동 재시도(투명). 결정 G 참조. |
| 낙관적 락 충돌 (같은 액션) | 한 명만 성공, 다른 한 명은 422 (이미 다음 단계로 진행됨) — 단순 재시도하지 않음. |
| 미인증 호출 (세션 쿠키·API key 모두 없음) | `AUTH_ENFORCEMENT=strict`에서 401, `soft`에서 통과 + `audit_violation` 기록(AUTH 계획서 결정 E). `off`(개발 모드)에서만 익명 통과. |
| 동일 액션에 대한 병렬 승인 시도 | `with_for_update()` + 낙관적 락 이중 방어. |
| 동일인이 CRM 후 Sales 승인 시도 | 422 (`segregation_of_duties`). 결정 C-1. `APPROVAL_ALLOW_SELF_CHECK=true` 환경변수가 있으면 우회 + `self_check_bypass` 이벤트 기록. |
| `nba_summary_approval` 미완료 상태에서 개별 action 승인 시도 | 422 ("분석 결과 전체에 대한 CRM/Sales 인지가 먼저 필요합니다"). 결정 H. |
| `_red_flag=true` 노트에서 파생된 NBA | UI에 red-flag 경고 배너. CRM이 승인 시 "red-flag 확인함" 체크박스 강제. |
| Sales 승인 후 연결 activity 자동 시작 | `pending` activity는 `in_progress`로 자동 전이 (`updated_by="system:sales_approved"`). 이미 `in_progress`/`completed`/`cancelled`는 미변경. `AUTO_START_ACTIVITY_ON_SALES_APPROVE=false`로 비활성화 가능. 결정 I. |
| Sales revoke 시 자동 시작된 activity 처리 | 자동 되돌림 안 함. UI에 "이 activity는 이전 sales 승인으로 시작되었습니다" 정보 표시만. 운영자 수동 결정. |

---

## 9. 실행 로드맵

> Phase -1은 **승인 워크플로우가 의미 있게 동작하기 위한 전제조건**이다. 이걸 건너뛰면 "누구나 승인 가능한 워크플로우"가 되어 내부통제 관점에서 무가치하다.
>
> ⚠️ **v3 변경**: 인증·사용자·권한 관련 Phase -1 항목들은 [`AUTH_USER_MANAGEMENT_PLAN.md`](./AUTH_USER_MANAGEMENT_PLAN.md)로 이전되었다. **AUTH 계획서의 Phase A·B·C가 완료된 시점이 본 계획서 Phase 0의 시작점**이다. 본 표에는 NBA 워크플로우에만 직접 관련되는 잔여 블로커만 남긴다.

### Phase -1. 블로커 해결 (승인 워크플로우 전제)

| 항목 | 근거 | 이 계획과의 관계 | 어디서 처리 |
|---|---|---|---|
| 인증/인가 (로그인·세션·역할·권한) | CodeReview `#Fix 3` | 결정 C 전체. `current_user` / `require_role` 의존성, 4-tier 역할, AUTH_ENFORCEMENT 단계 전환. | **[AUTH 계획서](./AUTH_USER_MANAGEMENT_PLAN.md) Phase A·B·C** (선행) |
| `escHtml` 완전 이스케이프 | CodeReview `#Fix 1` | 승인 근거 메모가 UI에 그대로 렌더됨 → XSS 직결. | **[AUTH 계획서](./AUTH_USER_MANAGEMENT_PLAN.md) Phase A** 와 동시 처리 (관리 UI에서도 동일 함수 사용) |
| 글로벌 `_model_setting`/`running_set` 제거 | CodeReview `#Fix 2` | 승인 워크플로우는 멀티유저 전제. 전역 공유 상태는 승인 이벤트의 선후관계를 망가뜨림. | **[AUTH 계획서](./AUTH_USER_MANAGEMENT_PLAN.md) Phase A**: 사용자별 세션 컨텍스트로 자연 분리 |
| f-string SQL → 파라미터 바인딩 | CodeReview `#Fix 4` | 승인 API의 `customer_id` / `action_id` 파라미터를 안전하게 처리. | **[AUTH 계획서](./AUTH_USER_MANAGEMENT_PLAN.md) Phase A** 와 동시 처리 (전반적 코드 리뷰) |
| `save_qc_report` 덮어쓰기 이슈와 같은 패턴을 `save_nba`/`save_activities`에도 적용 | CodeReview `#Improve 4` | 결정 D의 shallow merge가 이 이슈의 직접적 해결책. | **본 계획서 Phase 1** (AUTH 완료 후) |

### Phase 0. 스키마 확정 & 마이그레이션

- [ ] `src/db/database.py`에 `ApprovalEvent` 모델 추가
- [ ] `scripts/migrate_nba_schema.py` 작성·실행 (legacy 백업 포함, 액션 당 `migrated_from_legacy` 합성 이벤트 기록, `derived_from_plans` 정규식 추출, `version=1` 초기화)
- [ ] `scripts/migrate_activity_schema.py` 작성·실행 (제목 매칭 → `linked_action_id` 주입, 매칭 실패 건은 별도 리포트)
- [ ] 마이그레이션 후 `GET /api/customer/{cid}` 응답이 깨지지 않는지 smoke test
- [ ] `tests/` 디렉토리 신설 + `tests/conftest.py`에 SQLite 임시 DB fixture

### Phase 1. 서버 측 전이 로직 + 식별 안정화 + 자동 시작

- [ ] `src/tools/approval_tools.py` 신설 (`transition`, `reproject_activities`, `bulk_transition`, `auto_start_activities_on_sales_approve`)
- [ ] `src/tools/action_identity.py` 신설 (`normalize`, `compute_content_hash`, `match_existing_action(stable_key, content_hash, embedding)`, `get_or_create_embedding`)
- [ ] `save_nba`를 stable_action_key + content_hash + embedding 3단계 매칭 기반 merge로 리팩터링
- [ ] `save_activities`의 `activity_status` merge 보강 ("pending이 아니면 LLM 응답 무시" 규칙)
- [ ] NBAAgent 프롬프트: `stable_action_key` 명명 규칙 + 좋은/나쁜 예시 추가, `action_id` 출력 금지 명시, `derived_from_plans` 출력 강제, system message에 이전 액션 목록의 key 주입
- [ ] ActivityAgent 프롬프트: `linked_action_id` 필수, `crm_approved_by/sales_approved_by` LLM이 채우지 말고 null 명시
- [ ] Sales 승인 → 연결 activity `pending` → `in_progress` 자동 전이 (`AUTO_START_ACTIVITY_ON_SALES_APPROVE` 플래그, 기본 true)
- [ ] Maker-Checker 검증 로직 (`segregation_of_duties`)
- [ ] `nba_summary_approval` 전이 로직 + 개별 action 승인의 전제조건 검증
- [ ] **유닛 테스트** (`tests/test_approval_transition.py`):
  - 상태전이 매트릭스 전부
  - 낙관적 락 충돌 (다른 액션 / 같은 액션)
  - 재실행 보존 (key 일치 / 유사도 ≥ 0.92 / 유사도 < 0.92)
  - Maker-Checker 차단 + bypass 플래그
  - Summary sign-off 선행 강제
  - Sales 승인 시 activity 자동 시작
- [ ] `tests/test_action_identity.py`: 정규화·해시·임베딩 매칭 단위 테스트
- [ ] **메트릭**: NBA 동일 입력 3회 재실행 시 stable_action_key 동일 비율 ≥ 90% — 미달 시 프롬프트 재튜닝

### Phase 2. API 엔드포인트

- [ ] 4개 액션 전이 엔드포인트 + 2개 summary acknowledge + `events` 조회 + `pending` 쿼리(페이징·정렬·필터) + `bulk` 일괄(per_item / all_or_nothing) + `bulk-revoke-by-events` + `promote-variant`
- [ ] AUTH 계획서가 제공하는 `Depends(current_user)` + `Depends(require_role("crm"))` / `Depends(require_role("sales"))`를 본 워크플로우의 모든 mutation 엔드포인트에 부착
- [ ] `_run_bulk` 공통 헬퍼 (CodeReview `#Improve 1` 확장 — 본 워크플로우의 bulk 승인과 기존 NBA/Activity bulk 실행을 같은 헬퍼로)
- [ ] `tests/test_approval_api.py`: API 레벨 회귀 (httpx + fastapi.TestClient)

### Phase 3. UI

- [ ] 고객 상세: 분석 결과 sign-off 카드 + 각 NBA action 승인 버튼 + 근거 메모 모달 + 승인 이력 아코디언 + 변형 제안 토글 + 자동 시작된 activity 인디케이터
- [ ] 대시보드: 승인 대기 큐 위젯(페이징·필터) + 일괄 승인 체크박스(`per_item` 기본)
- [ ] red-flag 경고 배너 연동
- [ ] `escHtml` 수정본으로 전체 재검토 (Phase -1에서 수정된 것을 사용 — 근거 메모 렌더링)

### Phase 4. 운영 편의

- [ ] ChatAgent에 `get_pending_approvals(role)` read-only tool 추가
- [ ] `/api/approvals/export.csv` (내부 감사 대응, `self_check_bypass` 별도 섹션)
- [ ] Maker-Checker bypass 월간 리포트 자동 생성

### Phase 5. 강화

- [ ] ~~실제 SSO/세션 인증 (결정 C Phase 3)~~ — **AUTH 계획서로 이전됨** (Phase A·B·C에서 처리, 추후 Google SSO 마이그레이션은 AUTH 계획서 결정 G 참조).
- [ ] 알림 (CRM 승인 시 Sales에게, Sales 승인 시 담당자에게) — Slack/이메일 연동은 별도 계획.
- [ ] 메트릭 기반 잠금 단위 결정 G 옵션 (b)/(c) 마이그레이션 (분당 409 ≥ 1건 시).

---

## 10. 회귀 체크리스트 (수동 or 자동)

1. **AI 제안만**: NBA 최초 생성 → 모든 action `ai_proposed`, 모든 activity `nba_approval.status = ai_proposed`, `nba_summary_approval.status = ai_proposed`.
2. **Summary sign-off 선행 강제**: `nba_summary_approval`이 `ai_proposed` 상태에서 개별 action `crm-approve` 시도 → 422 ("분석 결과 인지 필요").
3. **CRM 승인 1건**: summary가 `crm_acknowledged` → 특정 action CRM 승인 → 해당 action `crm_approved` + 연결된 activity들의 mirror도 `crm_approved`로 갱신 + `_approval_projected_at` 업데이트.
4. **Sales 승인 1건**: 위 상태에서 다른 사람이 Sales 승인 → `sales_approved`로 전이 + mirror 전파 + 연결 activity의 `pending` → `in_progress` 자동 전이 (결정 I).
5. **Sales 직승인 차단**: `ai_proposed` 상태 action에 Sales 승인 요청 → 422.
6. **Maker-Checker 차단**: 동일인이 CRM 승인 후 Sales 승인 시도 → 422 (`segregation_of_duties`). `APPROVAL_ALLOW_SELF_CHECK=true` 환경에서는 통과 + `self_check_bypass` 이벤트 기록.
7. **재실행 보존 (stable_action_key 일치)**: 동일 input으로 NBA 재실행, key 동일 → `crm_approved` 유지, version 그대로, `last_seen_at`만 갱신.
8. **재실행 변형 분류 (key 미일치, 유사도 ≥ 0.92)**: `how_to` 표현만 살짝 바뀐 재실행 → 기존 action에 `variant_proposals[]` attach, 별도 카드 분리 X.
9. **재실행 신규 (유사도 < 0.92)**: 의미 변경 큰 재실행 → 기존 action `superseded`, 새 action `ai_proposed`.
10. **Sales 승인 freeze**: Sales까지 승인된 action은 `force=false` 재실행에서 `superseded`되지 않음.
11. **낙관적 락 충돌 (다른 액션)**: 같은 고객의 다른 action을 두 클라이언트가 동시 승인 → 한 건 즉시 성공, 다른 건 409 → 클라이언트 자동 재시도 1회 → 최종 둘 다 성공 (다른 액션이므로).
12. **낙관적 락 충돌 (같은 액션)**: 같은 action을 두 클라이언트가 동시 승인 → 한 건 성공, 다른 건 422 (이미 다음 단계로 진행).
13. **회수 플로우**: `sales_approved → sales_revoke` → `crm_approved`로 복귀 + activity mirror도 하향 조정. 단 자동 시작된 activity는 그대로 유지(결정 I).
14. **감사 로그 완결성**: 임의 시나리오 후 `approval_events` 시계열이 현재 `approval` 상태와 일치. 마이그레이션된 액션은 `migrated_from_legacy` 이벤트가 첫 줄에 존재.
15. **Bulk per_item**: 10건 중 1건 권한 부족 → HTTP 207, 9건 success / 1건 failed 응답.
16. **Bulk all_or_nothing**: 10건 중 1건 권한 부족 → HTTP 422, 모두 롤백.
17. **red-flag 경고**: `_red_flag=true` 노트에서 파생된 NBA를 CRM 승인 시도 → 확인 체크박스 없으면 422.
18. **action_plans 역참조**: 마이그레이션된 NBA에서 `derived_from_plans`가 비어 있지 않다 → `/api/customer/{cid}` 응답에 양방향 링크.
19. **Pending 큐 페이징**: 500건 환경에서 `?limit=50&offset=0&sort=due_date&dir=asc` 정상 응답 + `total` / `has_more` 일치.

---

## 11. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 인증 없이 승인 API 배포 | 내부통제 실패, 감사 지적 | Phase -1 완료 전에는 **원격 배포 금지**. 로컬에서만 테스트. |
| LLM이 `stable_action_key`를 일관성 없이 부여 | 승인 안정성 핵심 가정 붕괴 | (1) NBA 프롬프트에 키 명명 규칙 + 좋은/나쁜 예시 다수 제공. (2) 서버가 이전 액션 목록의 key를 system message에 넣어 "이 키 중 동일 의도가 있으면 같은 키 재사용" 명시. (3) 임베딩 fallback (유사도 ≥ 0.92)으로 이중 안전장치. (4) Phase 1에서 키 안정성 메트릭 측정(연속 3회 재실행에서 키 동일 비율) — 90% 미만이면 프롬프트 재튜닝. |
| 임베딩 API 비용/지연 | NBA 재실행 시 마다 임베딩 호출이 누적 | `actions_meta`에 임베딩 캐시. stable_action_key/content_hash로 1차 매칭하면 임베딩 호출 자체가 발생하지 않음. 신규 액션만 호출. |
| `activity_status` merge 실패로 진행 중 활동이 pending으로 되돌아감 | 실무자 실행 이력 소실 | merge 로직에 "activity_status가 pending이 아닌 경우 LLM 응답의 activity_status 무시" 규칙 명시 + 유닛 테스트. |
| Railway PostgreSQL 롤아웃 시 마이그레이션 순서 꼬임 | 기동 실패 | `Base.metadata.create_all`는 멱등. `scripts/migrate_*.py`는 `if old_schema` 감지 후에만 동작하므로 재실행 안전. 컬럼 추가는 본 워크플로우 범위 밖이므로 Alembic 미도입 OK. |
| `expected_version` 미제공 구현 실수로 덮어쓰기 발생 | 승인 상태 이상 | Pydantic 모델에서 필수화, 클라이언트가 보낸 값이 없으면 422. |
| CRM이 실수로 대량 승인 | 되돌리기 수작업 증가 | bulk 승인 응답에 `event_ids`를 반환하여 "방금 승인한 것들 일괄 회수" 엔드포인트 제공 (`POST /api/approvals/bulk-revoke-by-events`). |
| 동일인 self-check 우회 플래그 남용 | 직무 분리 원칙 무력화 | `self_check_bypass` 이벤트가 매 사용 시 기록되도록 강제. 월 1회 감사 리포트(`/api/approvals/export.csv`)에 별도 섹션으로 노출. 운영팀 정기 리뷰. |
| 한 customer row에 actions가 너무 많아 lock 경합 빈발 | 결정 G 옵션 (a)의 약점 노출 | 운영 메트릭 `approval_409_count_per_minute` 모니터링. 분당 1건 이상이면 결정 G 옵션 (b)로 마이그레이션 검토. |
| `nba_summary_approval` 단계가 사용자에게 번거로움 | UX 저항으로 우회·skip 압력 | UI에서 sign-off 버튼을 분석 결과 카드 헤더에 1클릭으로 배치. 메모는 선택. 마우스 1회 클릭으로 acknowledge 가능. |

---

## 12. 요약

프롬프트와 UI는 이미 3단계 승인 세계를 가정하고 써 있지만, **데이터·API·상태전이·권한·재실행 보존·직무 분리** 여섯 축은 아직 없다. 그중 **권한**(인증·역할·세션·API key·CSRF·rate limit·감사로그)은 CodeReview.md `#Fix 3`과 정면으로 맞닿아 있는 별도의 큰 작업이라, **[AUTH_USER_MANAGEMENT_PLAN.md](./AUTH_USER_MANAGEMENT_PLAN.md)로 분리**해 선행시킨다. AUTH 계획서의 Phase A·B·C가 끝난 시점이 본 계획서 Phase 0의 시작점이며, 본 계획의 모든 mutation API는 AUTH 계획서가 제공하는 `current_user` / `require_role` / CSRF / `AUTH_ENFORCEMENT` 인프라 위에서 동작한다.

그 위에 다음 9개 핵심 결정을 얹으면, LLM이 아무리 재실행되어도 승인 상태가 안정적으로 유지되는 실제 운영 가능한 워크플로우가 된다:

- **결정 A** — 서버 생성 `action_id` + LLM이 부여하는 `stable_action_key` + 정규화 `content_hash` + 임베딩 유사도(0.92) 3단계 매칭으로 LLM 표현 변화에 둔감한 액션 식별
- **결정 B** — NBA = 원본 / Activity = 투영(단방향 미러)
- **결정 C** — actor 식별 메커니즘은 [AUTH 계획서](./AUTH_USER_MANAGEMENT_PLAN.md)로 외부화 (`current_user` 의존성 + `require_role` 가드) + Maker-Checker 직무 분리 강제(`segregation_of_duties`)
- **결정 D** — content_hash 기반 shallow merge로 재실행 시 승인 상태 보존
- **결정 E** — append-only `approval_events` 단일 진실 출처 + 마이그레이션 합성 이벤트
- **결정 F** — `action_plans.json`(AP-CXXX-NNN)과 NBA action_id의 다대다 관계 명시 (`derived_from_plans`)
- **결정 G** — Phase 1은 거친 락 + 자동 재시도, Phase 3은 메트릭 기반 액션별 row 분리 검토
- **결정 H** — `nba_summary_approval`로 분석 결과 전체에 대한 별도 sign-off (개별 action 승인의 전제)
- **결정 I** — Sales 승인 시 연결 activity의 `pending` → `in_progress` 자동 전이

---

## 13. 개정 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| v1 | 2026-04-25 | 초안 작성 (5개 핵심 결정, 12개 회귀, 6개 리스크) |
| v2 | 2026-04-25 | 리뷰 반영. 결정 A를 `stable_action_key` + 정규화 + 임베딩으로 강화 (C1), 결정 C에 Maker-Checker 정책 추가 (C4), 결정 F·G·H·I 신규 추가 (C2/C3/S6/S7), 엣지케이스·리스크·회귀 체크리스트 보강, 로드맵을 테스트 위치 명시로 구체화 (S8), 첫 줄 메타데이터를 `agenticCRM_flow`로 정정 (S1). 핵심 결정 5개 → 9개. 회귀 12개 → 19개. 리스크 6개 → 9개. |
| v3 | 2026-04-25 | **AUTH 계획서 분리**. 인증·사용자·역할·세션·API key·CSRF·rate limit 관련 책임을 [`AUTH_USER_MANAGEMENT_PLAN.md`](./AUTH_USER_MANAGEMENT_PLAN.md)로 외부화. ① 결정 C에서 `X-Actor` 헤더·`users.json` 화이트리스트·SSO Phase 분리를 폐기하고 AUTH 계획서의 `current_user` / `require_role` 의존성 인프라 사용으로 변경. ② Phase -1 표를 "어디서 처리되는가" 컬럼 추가 형식으로 재구성, 인증 항목은 AUTH Phase A·B·C 선행으로 명시. ③ §5 API 공통 요구사항을 `Depends(current_user)` + `require_role` + CSRF 헤더 모델로 갱신, `X-Actor` 헤더 가정 제거. ④ Phase 5의 SSO 항목을 AUTH 계획서로 이전(strikethrough). ⑤ §12 요약을 AUTH 분리 사실에 맞춰 재작성. 본 계획서는 이제 "AUTH 인프라 위에 얹는 도메인 워크플로우"로 위치 재정의. |
