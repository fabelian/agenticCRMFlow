# 코드/사이트 리뷰 — 개선 항목 정리 (2026-04-25)

> **범위**: `agenticCRM_flow` 전체 (`web/app.py`, `src/agents/*`, `src/tools/data_tools.py`, `src/db/database.py`, `web/templates/index.html`, `web/templates/customer.html`)
> **대상**: <https://web-production-bce19.up.railway.app/> (라이브 사이트는 egress 차단으로 정적 분석만 수행)
> **기준일**: 2026-04-25 (KST)
> **작성자**: 코드 분석 패스 (CodeReview.md, NBA_APPROVAL_WORKFLOW_PLAN.md, AUTH_USER_MANAGEMENT_PLAN.md 보완)
> **분석 한정**: 본 문서는 **개선 항목 식별·분류**만 다룸. 구현 자체는 별도 PR/계획 단계에서 진행.

## 0. 요약

기존 3개 문서(`CodeReview.md`, `NBA_APPROVAL_WORKFLOW_PLAN.md`, `AUTH_USER_MANAGEMENT_PLAN.md`)에 이미 정리된 항목 외에 **신규로 발견된 주요 이슈 30+ 건**을 카테고리별로 정리했다. 가장 시급한 항목 7개는 §1 Critical에 모았으며, 각 항목 끝에는 “기존 문서 매핑”과 “재현/근거 위치”를 명시했다. 라이브 사이트 동적 검증은 본 세션에서 불가했으므로 §6에 추후 확인 권고 항목을 별도로 정리한다.

| 카테고리 | 신규 항목 수 | 기존 문서에서 일부 다루어진 항목 수 |
|---|---:|---:|
| 보안 (Security) | 6 | 3 |
| 안정성 / 동시성 (Reliability) | 7 | 1 |
| 성능 (Performance) | 5 | 1 |
| UX / 일관성 | 4 | 0 |
| 코드 품질 / 유지보수 | 6 | 2 |
| 운영 / 배포 (Ops) | 4 | 0 |

---

## 1. Critical (가능한 빨리 수정 권고)

### C-1. 세일즈 노트 테이블 다중 필드 미이스케이프 — Stored XSS

`web/templates/index.html:2810-2823`

```js
<td class="text-nowrap">${n.Activity_Date || n.date || '—'}</td>
<td><a href="/customer/${n._customer_id}" ...>${n._customer_name || '—'}</a></td>
<td class="small text-muted">${n.Sales_Name || '—'}</td>
<td><span class="badge ...">${n.Activity_Type || '—'}</span></td>
<td class="small">${n.Sector || '—'}</td>
<td class="small text-muted">${n.Contact_Name || '—'}</td>
<td class="text-truncate-cell small text-muted">${n.Action_Point || '—'}</td>
```

`Activity_Date`, `_customer_name`, `Sales_Name`, `Activity_Type`, `Sector`, `Contact_Name`, `Action_Point` 7개 필드가 **`escHtml` 없이 직접 innerHTML 삽입**된다. 이 필드들은 `/api/sales-notes/upload` 또는 `POST /api/sales-notes`로 사용자가 임의 텍스트를 넣을 수 있다 → 저장형 XSS.

대화 로그 형태인 `Action_Point`는 자유 텍스트 → `<img src=x onerror=...>` 같은 페이로드가 그대로 실행된다.

- **기존 문서 매핑**: CodeReview #Fix 1은 `escHtml`이 작은따옴표를 이스케이프하지 않는 점만 지적했으나, **호출 자체가 빠진 케이스**는 별개의 더 심각한 이슈다.
- **권고**: 테이블 행 렌더 시 모든 사용자 데이터에 `escHtml` 적용 + `escHtml`을 `&'"`까지 이스케이프하도록 통일 (customer.html 412 라인은 이미 `"`까지 처리, index.html 3562 라인은 `&<>`만 처리 — 통일 필요).

### C-2. 인증·인가 부재 — 누구나 모든 데이터 CRUD 가능

`web/app.py` 전체 라우트

`/api/customers` POST/DELETE, `/api/sales-notes` POST/DELETE, `/api/run/*` (전체 LLM 비용 발생) 등 어떤 인증도 없이 호출 가능. AUTH_USER_MANAGEMENT_PLAN.md에 “Phase 1: 로그인 폼 + 세션 쿠키” 정의되어 있으나 **현재 배포에는 미적용**.

- **기존 문서 매핑**: CodeReview #Fix 3 + AUTH plan 전체.
- **즉시 조치(Phase 0 미니멈)**: Railway 환경변수 `BASIC_AUTH_USER` / `BASIC_AUTH_PASS`로 라이브 데모를 임시 보호. AUTH plan Phase 1 도입 전까지의 임시 차단.
- **운영 비용 위험**: `/api/run/persona-all` 같은 일괄 SSE는 외부에서 GET 한 번으로 11명×Opus 호출이 트리거됨. 봇 크롤링·검색 인덱서가 우연히 hit하면 즉시 비용 폭발.

### C-3. SSE 스트림이 `sys.stdout`을 전역 교체 — 동시 실행 시 로그 교차 오염

`web/app.py:202-211`, `222-245` (`run_pipeline`, `run_single_agent` 내부)

```python
sys.stdout = StreamCapture(q)   # 전역 교체!
try:
    orchestrator.run(...)
finally:
    sys.stdout = original_stdout
```

서로 다른 두 클라이언트가 동시에 `/api/analyze/{id}`를 열면, 한 쪽 SSE에 다른 쪽 에이전트 로그가 끼어든다. 특히 Persona/NBA 결과 텍스트가 다른 고객 화면으로 새는 정보 누출.

- **기존 문서 매핑**: 신규.
- **권고**: `contextvars` + 에이전트 `_log` 콜백 주입으로 “이 실행만의 로거”를 전달. `print()`-기반 로깅은 백엔드 stdout 전용으로 두고 SSE는 명시적 이벤트 큐만 사용.
- **위험도**: 고객 데이터가 다른 사용자에게 노출될 수 있는 **데이터 격리 위반**.

### C-4. `add_sales_note` 비원자적 채번 — 동시 업로드 시 `note_id` 중복

`src/tools/data_tools.py:239-256`

`max(note_id) + 1` → insert 시퀀스가 트랜잭션 밖. `/api/sales-notes/bulk-commit` + `/api/sales-notes` 동시 호출 시 같은 `note_id`로 두 row가 들어가거나 중복키 예외 발생.

- **기존 문서 매핑**: 신규.
- **권고**: PK 채번을 `uuid4().hex[:12]` 또는 DB sequence로 전환. 로컬 SQLite에서도 안전.

### C-5. CSRF 보호 부재 — 외부 사이트가 사용자 세션으로 변경 작업 가능

`web/app.py` POST/DELETE 전체 + 향후 도입될 인증 쿠키

CSRF 토큰 발급/검증 코드가 전혀 없다. 인증이 도입되면 **즉시 CSRF 취약점이 됨**: `<form action="https://...up.railway.app/api/customers/C001" method="post">` 만으로 로그인된 사용자가 데이터 삭제됨.

- **기존 문서 매핑**: AUTH plan은 “세션/JWT”까지 명시하지만 CSRF 토큰 명시는 약함. NBA plan v3 §C는 CSRF를 “AUTH plan에 위임”으로만 표시.
- **권고**: AUTH plan Phase 1과 동시에 SameSite=Lax/Strict 세션 쿠키 + `X-CSRF-Token` 더블 서브밋 토큰을 명시. 모든 mutating 라우트에 미들웨어 강제.

### C-6. GET이 mutation을 트리거 — REST 위반 + 봇/프리페치 비용 폭발

`web/app.py` `/api/run/persona-all`, `/api/run/nba-all`, `/api/run/activity-all`, `/api/run/qc-all` 모두 GET.

브라우저 prefetch (`<link rel=prefetch>`), Slack/Discord 링크 미리보기 봇, 검색엔진 크롤러가 한 번 hit하면 **전 고객 LLM 일괄 실행**이 트리거된다. SSE이라 응답을 끊지 못해 끝까지 비용 발생.

- **기존 문서 매핑**: 신규.
- **권고**: GET → POST 변환. 내부 SSE는 “POST로 작업 등록 → GET으로 진행 스트림 구독”의 2-콜 패턴이 일반적. CodeReview #Improve 1과 묶어 처리 가능.

### C-7. Output 디렉토리(`output/`)가 Railway 영속 스토리지 아님 — 보고서 유실

`src/agents/orchestrator.py:190` (`output_dir.mkdir(exist_ok=True)`) + `src/tools/data_tools.py:31` (모듈 임포트 시 mkdir)

Railway 컨테이너 재시작·재배포 시 `output/`이 사라진다. 사용자에게는 “보고서 저장됨” 알림이 가지만 다음 배포 후 404.

- **기존 문서 매핑**: 신규.
- **권고**: 보고서를 DB(`reports` 테이블) 또는 S3-호환 오브젝트 스토리지로 이동. `OUTPUT_DIR.mkdir`을 모듈 임포트 시점에서 빼고 “쓸 때만 mkdir” + “쓸 수 없으면 DB 폴백”.

---

## 2. High (다음 스프린트 권고)

### H-1. 에이전트 인스턴스의 가변 상태 (`self._since_date`, `self._results`)

`src/agents/persona_agent.py:77`, `nba_agent.py` 동일 패턴, `dislike_checker_agent.py:84`

`run()`에서 인스턴스 속성에 `since_date`/`results`를 저장. 같은 에이전트 인스턴스를 동시에 두 호출이 공유하면 race. 현재 코드는 매 호출마다 새 인스턴스를 만들어 “우연히” 안전하지만, 추후 인스턴스 풀링/재사용 시 즉시 깨진다.

- **권고**: `run(..., since_date=None)` 인자를 도구 호출 시 클로저/딕셔너리로 캡처하거나, `execute_tool`이 `extra_context: dict`를 받도록 시그니처 확장.

### H-2. `update_activity_field` `nba_approval` 분기에서 `updated_at` 누락

`src/tools/data_tools.py:593-598`

`activity_status` 분기는 `updated_at`을 set 하지만(`591`) `nba_approval` 분기는 빠져 있어 NBA 승인 상태가 바뀌어도 ‘언제 바뀌었는지’ 추적이 안 됨.

- **NBA plan v3 영향**: §3 “감사 로그” 요건과 직결. plan은 별도 audit row에 시간을 기록하지만, 현재 단일 `nba_approval` 객체에서도 timestamp 필드가 의도되어 있다면 누락이다.
- **권고**: `nba_approval.updated_at = now_kst_str()` 추가, 또는 NBA plan v3 §3.2 audit log 도입과 함께 객체 자체의 last-mutation timestamp 동기 보장.

### H-3. 모든 `save_*`가 전체 overwrite

`src/tools/data_tools.py` `save_persona/save_nba/save_activities/save_qc_report` 전부

증분 필드 업데이트가 없음. 두 사용자가 동시에 같은 고객의 다른 탭을 수정하면 last-write-wins.

- **기존 문서 매핑**: CodeReview #Improve 4(QC만 명시) → 모든 save_* 함수로 확장.
- **권고**: `optimistic locking version` 필드(NBA plan v3 §3.1과 동일 패턴)를 모든 결과 테이블로 일반화. 단순 단일 사용자 환경이면 보류 가능.

### H-4. `get_recent_notes_with_weights` 월 수 → days = months × 30

`src/tools/data_tools.py:662-664`

`months=6` → `days=180` → 7월 1일에 1월 2일 노트가 잘림. 의도된 “6개월 이내” 의미와 어긋남(달력월 ≠ 30일).

- **권고**: `dateutil.relativedelta` 또는 (year, month) 쌍으로 컷오프 계산. CRM·증분 페르소나 갱신의 경계 케이스에 영향.

### H-5. `seed_customers_if_empty` 이름과 동작 불일치 — 데이터 덮어쓰기 위험

`src/tools/data_tools.py:50-118` (CodeReview #Fix 5)

이름은 “비어있을 때만”인데 실제로는 항상 JSON → DB upsert. 로컬 SQLite 재시작 시 웹에서 수정한 고객 데이터가 JSON 시드로 덮인다.

- **권고**: 이름을 `sync_customers_from_json` 으로 변경 + “DB의 last_modified > JSON last_modified면 스킵” 분기 추가, 또는 lifespan에서 `if env != "production"` 가드.

### H-6. async 라우트에서 동기 DB 호출

`web/app.py` 모든 `async def` 라우트가 `dt.get_*` (sync SQLAlchemy) 호출

이벤트 루프 블로킹. 고객 50명 + 동시 SSE 5건 시 응답 지연 확실.

- **권고**: `def`(sync) 라우트로 전환하거나 `run_in_threadpool` 명시적 호출. FastAPI는 `def`로 정의하면 자동 threadpool 사용.

### H-7. 일괄 SSE 동시기동 방지의 race (`running_set`)

`web/app.py` `running_set` (CodeReview #Fix 2 일부 다룸)

`if key in running_set: return ...` 후 별 스레드에서 add 사이의 race. 두 클라이언트가 같은 시각에 `/api/run/persona-all`을 누르면 둘 다 통과.

- **권고**: `threading.Lock` 또는 `asyncio.Lock` (사이클은 SSE 동기 generator라 thread Lock이 적합). DB 측 advisory lock도 고려.

---

## 3. Medium (개선 권고)

### M-1. SSE heartbeat 폴링이 100ms 회전

`web/app.py:_agent_sse` 루프의 `q.get(timeout=0.1)`

클라이언트당 초당 10회 “비어있는 큐” 깨우기. 클라 5명만 붙어도 백엔드 50 wakeup/sec — Railway free/hobby tier에서 CPU 비용·로그 노이즈 발생.

- **권고**: heartbeat 30s 주기 별도 타이머로 분리, `q.get(timeout=15)`로 키프얼라이브 간격 늘리기.

### M-2. `messages.create` 전체 traceback이 SSE 클라이언트로 누출

`src/agents/base_agent.py:140-150`, `268-272`

도구 실행 오류 시 `traceback.format_exc()`를 그대로 LLM tool_result + stdout(→ SSE)으로 흘림. 내부 경로·라이브러리 버전 정보 노출.

- **권고**: 클라용은 `error_msg`만, 내부 로그용은 별도 logger로 분리.

### M-3. 한국어 continuation 프롬프트 하드코딩

`src/agents/base_agent.py:119`, `234`

`"계속 작성해주세요. 중단된 부분부터 이어서 완성해주세요."` 영어 모드/외국어 사용자 대응 시 잠금. 사용성 영향은 작지만 i18n 시작 시 즉시 충돌.

### M-4. Anthropic 경로에 429 재시도 없음

`base_agent.py:_run_anthropic`

`_openrouter_create_with_retry` 와 같은 백오프가 Anthropic 호출에는 없음. Opus는 분당 RPM 한도가 빡빡하므로 일괄 SSE에서 발생할 수 있다.

### M-5. `get_sales_note` 단건 조회가 전체 스캔

`src/agents/chat_agent.py:252-260`, CodeReview #Improve 3 동일 라인

ChatAgent가 “이 노트만 보여줘”에 응할 때마다 전체 고객 → 전체 노트 순회. SQLAlchemy `session.get(SalesNote, note_id)` 한 줄로 대체 가능.

### M-6. Pydantic 모델 제약 부재

`web/app.py` `CustomerCreate` 등

`aum_billion_krw: float | None` — 음수, NaN, 1e308 모두 통과. `tier` 등 enum 필드도 `str` 그대로.

- **권고**: `Field(ge=0, le=1e6)`, `Literal["S","A","B"]` 적용.

### M-7. 입력 페이로드 사이즈 제한 없음

`web/app.py /api/sales-notes/upload`

대용량 JSON 업로드 시 메모리 폭주. uvicorn 기본은 무제한.

### M-8. SQLAlchemy 엔진에 `pool_pre_ping=True` 미설정

`src/db/database.py:21`

Railway PostgreSQL 유휴 연결이 끊긴 뒤 첫 요청에서 `OperationalError`. 사용자가 보는 “500 Internal Server Error” 1회 발생.

### M-9. 마이그레이션 시스템 부재

`src/db/database.py`

Alembic 미사용. JSON 컬럼 스키마 변경 시 무방비. NBA plan v3에서 `nba_approval` 객체 구조가 진화할 예정이므로 곧 필수.

---

## 4. Low (선택적)

### L-1. `OUTPUT_DIR.mkdir`이 모듈 임포트 시점 부수효과

`data_tools.py:31`

읽기 전용 환경(예: 프로덕션 컨테이너 root fs 잠금) 시 임포트 자체가 실패.

### L-2. `escHtml` 두 구현체 분리

`index.html:3562` vs `customer.html:412` — `&<>` vs `&<>"`. 단일 파일로 통합 + 유틸 모듈화.

### L-3. 매직 상수 산재

`max_continuations=5`, `max_tokens=16000`, `note_summary` preview 120자 등이 전부 인라인 상수.

### L-4. 정적 JSON과 DB의 이중 진실 소스

`data/customers.json` + `customers` 테이블이 둘 다 “정답 후보”. JSON을 단순 부트스트랩 시드로 명시하고 운영 중에는 읽지 않도록 정리.

### L-5. `get_persona/get_nba/get_qc_report` 등 광역 `except: return None`

`data_tools.py:421-422` 등 다수

DB 연결 오류·스키마 불일치를 모두 “데이터 없음”으로 가린다. 운영자가 5분간 “왜 페르소나가 안 보이지?”로 디버깅한다.

### L-6. 라이브 사이트 검증 미수행 항목 — §6 참고

---

## 5. 카테고리별 인덱스 (단건별 ID 매핑)

### Security

- C-1, C-2, C-5, C-6, M-2

### Reliability / Concurrency

- C-3, C-4, H-1, H-2, H-3, H-7

### Performance

- M-1, M-5, H-6, M-8

### UX / Consistency

- L-2, L-3, L-4, M-3

### Code Quality / Maintenance

- L-1, L-5, M-6, M-9, H-5

### Ops / Deployment

- C-7, H-5, M-9, M-7

---

## 6. 라이브 사이트 추가 점검 권고 (정적 분석으로 잡지 못한 영역)

본 세션에서 Railway URL은 egress 차단으로 직접 조회 불가했다. 다음 항목은 **사용자 환경에서 직접 확인** 부탁:

| # | 점검 항목 | 확인 방법 |
|---|---|---|
| LV-1 | CSP / X-Frame-Options / X-Content-Type-Options 헤더 존재 여부 | DevTools Network → 메인 문서 응답 헤더 |
| LV-2 | `Set-Cookie` 의 `Secure`/`HttpOnly`/`SameSite` 속성 | DevTools → Application → Cookies |
| LV-3 | 일괄 SSE 실행 시 클라 동시 두 탭 → 로그 교차 (C-3 재현) | 두 브라우저로 같은 시각 `/api/run/persona-all` 호출 |
| LV-4 | 사용자 입력 `Action_Point` 에 `<img src=x onerror=alert(1)>` 입력 → 노트 탭 진입 시 alert 발생 (C-1 재현) | 조심해서 자기 데모 환경에서만 |
| LV-5 | 인증 미적용 상태 그대로 → 외부 IP 에서 `curl -X POST .../api/customers ...` 통과 (C-2 재현) | curl |
| LV-6 | 프리페치 차단 확인 (C-6) | 외부 사이트에서 `<link rel=prefetch href=.../api/run/persona-all>` 삽입 후 비용 발생 여부 |

---

## 7. 기존 문서와의 매핑 표

| 본 문서 ID | 기존 문서 항목 | 관계 |
|---|---|---|
| C-1 | CodeReview #Fix 1 | 보강 (호출 누락 케이스 추가) |
| C-2 | CodeReview #Fix 3 + AUTH plan | 동일 영역, 임시 차단 권고 추가 |
| C-3 | — | 신규 |
| C-4 | — | 신규 |
| C-5 | AUTH plan §보안 | 보강 (CSRF 더블 서브밋 토큰 명시) |
| C-6 | — | 신규 |
| C-7 | — | 신규 |
| H-1 | — | 신규 |
| H-2 | NBA plan v3 §3 | 보강 (current `update_activity_field` 누락) |
| H-3 | CodeReview #Improve 4 | 일반화 |
| H-4 | — | 신규 |
| H-5 | CodeReview #Fix 5 | 동일 |
| H-6 | — | 신규 |
| H-7 | CodeReview #Fix 2 | 보강 (race 명시) |
| M-1 ~ M-9 | 일부 #Improve 1, 3 외 신규 | 혼합 |
| L-1 ~ L-6 | 대부분 신규 | — |

---

## 8. 권고 우선순위 (제안)

1. **이번 주**: C-2 임시 BasicAuth + C-6 GET→POST + C-1 escHtml 통일 + C-7 output 디렉토리 DB 이전 (모두 외부 노출 위험 제거).
2. **다음 스프린트**: AUTH plan Phase 1 본 도입 + NBA plan v3 Phase -1·0 (감사 로그 + state machine) → C-5, H-2 동시 해결.
3. **백로그**: H-1, H-3 (인스턴스 가변 상태 정리, 광역 overwrite → optimistic locking) + M-1, M-5, M-8 (성능/안정성 미세조정) + Alembic 도입 (M-9).

---

## 9. 부록: 본 리뷰에서 다루지 않은 영역

- 라이브 사이트 동적 검증 (egress 차단 — §6에서 사용자에게 위임)
- 프론트엔드 i18n / 접근성(a11y)
- 비용 분석 (모델별 호출량 측정)
- 로드 테스트 / 부하 시나리오
- 외부 SSO/OAuth 통합 (AUTH plan Phase 5)
