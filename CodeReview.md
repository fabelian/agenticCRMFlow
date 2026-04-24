# Code Review — testCRM_multiagent

리뷰 범위: `src/**`, `web/app.py`, `web/templates/*.html` (2026-04-24 `cd89cb0` 기준).

---

## ✅ Keep (잘 구현된 점)

### 1. OpenRouter HTTP 200 + 빈 `choices` 가드 (`src/agents/base_agent.py:200-214`)
```python
if response is None or not getattr(response, "choices", None):
    ...
    raise RuntimeError(f"OpenRouter 응답에 choices가 없습니다 ... details={error_info}")
```
OpenAI SDK가 silent 통과시키는 업스트림 장애 — `{"error": {...}}` 본문 — 를 명시적 예외로 바꿔 상위 에이전트 루프·SSE 핸들러가 일관되게 처리하도록 만든다. 커밋 `b3d2522`(prod 이슈 기반)의 실전 교훈을 코드에 고정시킨 방어 장치.

### 2. DislikeCheckerAgent의 "누락 보강망" (`src/agents/dislike_checker_agent.py:143-157`)
```python
returned = {r["note_id"]: r for r in self._results}
for n in notes:
    nid = n.get("note_id", "")
    if nid in returned:
        final.append(returned[nid])
    else:
        final.append({..., "is_red_flag": False, "reason": "에이전트 응답 누락 — 기본 false"})
```
LLM이 일부 `note_id`를 빠뜨리거나 도구 호출 자체를 생략하는 비결정성을 실체적으로 방어. 영속화 직전 무결성 보정 — "없으면 무시"가 아닌 "없으면 false로 명시"로 감사 추적 보존.

### 3. Persona/NBA 증분 필터 `since_date`의 end-to-end 전파 (`src/agents/persona_agent.py:77-93`, `nba_agent.py:107-113`, `data_tools.py:587-595`)
```python
# web/app.py
since_date = persona.get("updated_at") if persona else None
return _agent_sse(customer_id, "persona", since_date)
# persona_agent.py
self._since_date = since_date
# data_tools.py
cutoff = datetime.strptime(str(since_date)[:10], "%Y-%m-%d")
```
저장된 타임스탬프 그대로(시분 포함 문자열)를 다음 실행의 컷오프로 순환. 사용자 조작 없이 증분 업데이트가 성립하며, `:10` 슬라이스로 `"YYYY-MM-DD"` / `"YYYY-MM-DD HH:MM"` 두 포맷 자동 수용.

### 4. Activity envelope + `_unwrap_activities`의 후방 호환 처리 (`src/tools/data_tools.py:471-502`)
```python
envelope = {"activities": activities, "updated_at": now_kst_str()}
...
def _unwrap_activities(data) -> list:
    if isinstance(data, dict) and "activities" in data:
        return data.get("activities") or []
    if isinstance(data, list):
        return data
    return []
```
`updated_at`을 얹은 스키마 전환을 레거시 리스트 데이터 삭제 없이 달성. 배포 순서 꼬임/롤백 모두에서 `get_activities()`·`get_all_activities()`가 안전.

### 5. 전체 NBA 조회에서 red-flag 메타 서버 사이드 조인 (`web/app.py:852-870`)
```python
for note in dt.get_sales_notes(cid) or []:
    if note.get("note_id") == cmp_note_id:
        n["_cmp_note_flag"] = {"is_red_flag": bool(note.get("_red_flag")), ...}
```
프론트가 고객별로 `/api/sales-notes/{id}`를 N+1 호출해 플래그를 조회하는 대신 백엔드가 매칭 노트의 `_red_flag*`를 NBA 레코드에 병합. "경고 표시에 추가 LLM 호출 없음" 설계 원칙이 실제 데이터 플로우에 반영됨.

---

## ⚠️ Improve (개선하면 좋은 점)

### 1. 4개의 bulk 엔드포인트가 거의 동일 (`web/app.py:1018-1210`)
`_run_persona_all_thread` / `_run_nba_all_thread` / `_run_activity_all_thread` / `_run_qc_all_thread` 그리고 각각의 `api_run_*_all` — 총 ~300줄이 `for` 루프 · 스킵 조건 · 에이전트 클래스 한 줄씩만 다르고 나머지 동일. SSE 래퍼도 `running_set` 키 문자열과 agent 선택만 다름.

**수정안 스니펫:**
```python
def _run_bulk(
    agent_cls, customers, q, model, provider,
    skip_check,          # (cid) -> str | None (스킵 이유 혹은 None)
    agent_kwargs_fn,     # (cid) -> dict (force/since_date 등)
):
    total = len(customers)
    for i, c in enumerate(customers, 1):
        cid = c.get("customer_id", "")
        name = c.get("company_name", cid)
        try:
            reason = skip_check(cid)
            if reason:
                q.put({"type": "progress", "index": i, "total": total,
                       "customer_id": cid, "company_name": name,
                       "status": "skipped", "error": reason})
                continue
            q.put({"type": "progress", ..., "status": "started"})
            agent_cls(model=model, provider=provider).run(cid, **agent_kwargs_fn(cid))
            q.put({"type": "progress", ..., "status": "done"})
        except Exception as exc:
            import traceback; traceback.print_exc()
            q.put({"type": "progress", ..., "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"})
    q.put(None)

def _bulk_sse(lock_key: str, runner_fn) -> StreamingResponse:
    """running_set 체크 + 스레드 기동 + SSE 스트림 생성까지 공통화."""
    ...
```
4개 엔드포인트가 각자 `(agent_cls, skip_check, agent_kwargs_fn)` 3가지만 넘겨 재사용.

### 2. `startBulk*Update` 프론트 함수도 동일하게 중복 (`web/templates/index.html`)
4개 핸들러가 DOM id와 엔드포인트만 다르고 내부 SSE 이벤트 처리 로직은 그대로. 공통 팩토리:
```js
function makeBulkHandler({ btnId, panelId, barId, textId, listId, endpoint, tabKey, barClass, confirmMsg }) {
  return function start() {
    if (!confirm(confirmMsg)) return;
    ...
    es.onmessage = (e) => { /* 공통 progress/done/error 처리 */ };
  };
}
const startBulkQcUpdate = makeBulkHandler({ btnId:'btnBulkQc', ..., endpoint:'/api/run/qc-all', tabKey:'qc', ... });
```

### 3. `ChatAgent.get_sales_note`가 전체 노트 스캔 (`src/agents/chat_agent.py:252-260`)
```python
for c in customers:
    for n in dt.get_sales_notes(c.get("customer_id", "")) or []:
        if n.get("note_id") == nid:
            return n
```
고객 11명 × 노트 수십 건이면 문제 없지만, SalesNote 테이블 PK가 `note_id`이므로 단건 조회 헬퍼를 추가해 한 번의 질의로 끝낼 수 있음:
```python
# data_tools.py
def get_sales_note_by_id(note_id: str) -> dict | None:
    from db.database import SalesNote
    with _session() as session:
        row = session.query(SalesNote).filter_by(note_id=note_id).first()
        return row.data if row else None
```

### 4. `save_qc_report`가 기존 data를 통째로 덮어씀 (`src/tools/data_tools.py:540-551`)
```python
existing.data = report   # 과거 report의 추가 필드가 모두 사라짐
flag_modified(existing, "data")
```
QC 보고서에 외부에서 수동 주석/메타 필드를 얹는 시나리오가 생기면 유실. `save_persona`/`save_nba`도 동일 패턴이지만 QC는 검수 메타가 붙을 가능성이 상대적으로 높음. 얕은 병합으로 전환:
```python
existing.data = {**(existing.data or {}), **report}
```

### 5. `index.html` 단일 파일 2,800+ 줄
대시보드 본체 · 7개 탭 · 모든 JS · 모든 CSS · 모달 5개가 단일 템플릿에 포함. 탭 단위 `templates/partials/tab_activities.html` 로 나누거나 JS를 `web/static/dashboard.js`로 추출하면 diff/캐시/관심사 분리 모두 이득. Bootstrap 번들 외 자체 JS는 브라우저 캐시도 못 타고 매 요청 재전송 중.

---

## 🔥 Fix (반드시 고쳐야 할 점)

### 1. `escHtml`이 단일/이중 따옴표 미이스케이프 — JS injection 가능
**위치**: `web/templates/index.html:3497-3499`
```js
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
```
`&<>`만 이스케이프하고 `"`·`'`·백틱 모두 미이스케이프. 그런데 같은 파일에서 JS 문자열 컨텍스트에 삽입되는 케이스가 다수:

- `index.html:1967` — `onclick="openActivityDetailByKey('${escHtml(key)}')"`
- `index.html:2036` — `onclick="event.stopPropagation(); openActivityDetailByKey('${escHtml(key)}')"`
- `index.html:2061` — `onclick="... openActivityDetailByKey('${escHtml(key)}')">`
- 다수의 `title="${escHtml(x)}"` — 이건 `"`도 미이스케이프라 attribute 탈출 가능

**재현 시나리오**:
1. LLM이 생성한 Activity에 `id = "ACT-X'); alert(document.cookie);//"` 같은 문자열이 들어가거나, 악의적 사용자가 고객 id/회사명에 `'`를 포함시킨다.
2. `renderActivityList()`가 `openActivityDetailByKey('ACT-X'); alert(document.cookie);//')`로 렌더.
3. 해당 행 클릭 시 JS 삽입 실행.

현재 고객 id는 `C\d+`로 통제되지만 activity id / 회사명 / note id는 LLM·DB·CSV 업로드 경로 모두에서 들어와 완전 통제 불가.

**수정안**:
```js
function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
```
(참고: `customer.html:408`에는 이미 `"` 이스케이프가 있는 별개 버전이 존재 — 두 파일 버전이 어긋나 있음. 통일 필요.)

더 근본적으로는 인라인 `onclick="...('${...}')"` 패턴을 버리고 `data-key`와 이벤트 위임으로 리팩터:
```js
row.innerHTML = `<tr data-key="${escHtml(key)}">...</tr>`;
// 한 번만:
body.addEventListener('click', e => {
  const tr = e.target.closest('tr[data-key]');
  if (tr) openActivityDetailByKey(tr.dataset.key);
});
```

---

### 2. 전역 가변 상태 `_model_setting` / `running_set` — 요청 간 공유
**위치**: `web/app.py:176, 179`
```python
_model_setting: dict[str, str] = {"model": "google/gemma-4-26b-a4b-it:free"}
running_set: set[str] = set()
```
- 공개된 Railway URL(README의 라이브 데모)에서 여러 사용자가 동시에 접속 중. 사용자 A가 `POST /api/model`로 모델을 Opus로 바꾸면 사용자 B의 이후 `/api/chat` · `/api/analyze/*` 호출도 Opus로 결제된다.
- `running_set`도 check-then-act 레이스: `if "qc-all" in running_set` 체크와 `running_set.add(...)` 사이에 다른 요청이 들어오면 두 개의 qc-all 스레드가 동시 기동 가능.

**재현 시나리오**:
1. 공개 URL에서 사용자 A가 Opus 선택 → `_model_setting["model"] = "claude-opus-4-6"`
2. 사용자 B가 챗 사이드바에 "질문"을 입력 → 서버가 A의 Opus를 사용해 **A의 API 키로** 응답. 비용 폭증 + 의도치 않은 모델 사용.
3. 또는: 두 관리자가 동시에 "전체 QC 검수" 클릭 → 둘 다 `"qc-all" not in running_set` 통과 → 파이프라인 2번 돌아감 + 비용 2배.

**수정안 (최소)**: 세션별 모델 선택으로 격리
```python
# 모델 선택은 쿠키/세션/클라이언트 state로 이동.
# 서버는 요청마다 body/header에서 받아 쓰고, 서버 메모리에 저장 금지.
@app.post("/api/chat")
async def api_chat(body: ChatRequest):
    selected = body.model or DEFAULT_MODEL  # 클라가 매번 지정
    ...
```
`running_set`은 `threading.Lock()`으로 감싸거나, 정확히 한 번 실행이 필요하면 FastAPI의 `BackgroundTasks` + DB의 `active_jobs` 테이블로:
```python
_running_lock = threading.Lock()

def _acquire(key: str) -> bool:
    with _running_lock:
        if key in running_set: return False
        running_set.add(key); return True
```

---

### 3. 인증 전무 — 누구나 모든 엔드포인트 호출 가능
**위치**: `web/app.py` 전체. `app.add_middleware(AuthenticationMiddleware, ...)` 같은 코드 없음.

**재현 시나리오**:
라이브 데모 URL을 아는 누구든:
```bash
curl https://agenticcrm-production-cbeb.up.railway.app/api/run/persona-all?force=true
```
→ 전체 고객 × 강제 재생성 × Opus 호출. ANTHROPIC_API_KEY가 결제하는 금액 직접 증가. `DELETE /api/customers`, `DELETE /api/sales-notes`도 동일하게 무방비.

**수정안**:
- 최소한 `APP_PASSWORD` 환경변수 기반 HTTP Basic Auth 미들웨어를 붙이고 Railway 변수에 추가.
- 또는 Railway의 custom domain에 CloudFlare Access / Tailscale 붙여 IP/이메일 제한.
- 코드 레벨:
```python
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

security = HTTPBasic()
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASS = os.environ["APP_PASSWORD"]  # 필수화

def auth(creds: HTTPBasicCredentials = Depends(security)):
    if not (secrets.compare_digest(creds.username, APP_USER)
            and secrets.compare_digest(creds.password, APP_PASS)):
        raise HTTPException(401)

app = FastAPI(..., dependencies=[Depends(auth)])  # 전 엔드포인트
```

---

### 4. `/api/debug`의 f-string SQL (`web/app.py:312`)
```python
for table in ["customers", "sales_notes", "personas", "nba_results", "activities", "qc_reports"]:
    if table in info["tables"]:
        row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
```
현재는 하드코딩 리스트이고 `inspector.get_table_names()` 검증도 거치지만:
- 리스트가 미래에 유저 입력 기반으로 확장되면 바로 SQL injection으로 변질.
- 정적 분석(bandit/ruff 등) 경고 유발.

**수정안**: identifier 바인딩 지원하는 SQLAlchemy의 `quoted_name` 또는 `literal_column`:
```python
from sqlalchemy import literal_column, select, func
from db.database import engine, Customer, SalesNote, ...

TABLE_MODELS = {"customers": Customer, "sales_notes": SalesNote, ...}
for name, Model in TABLE_MODELS.items():
    if name in info["tables"]:
        info["row_counts"][name] = session.query(func.count()).select_from(Model).scalar()
```
테이블명 문자열 합성이 완전히 사라지고 ORM로 처리.

---

### 5. `seed_customers_if_empty` 이름 ↔ 동작 불일치 (`src/tools/data_tools.py:50-118`)
함수명은 "비어있을 때만 시드"를 시사하지만 실제 본문은 무조건 upsert:
```python
for customer in customers:
    ...
    if existing:
        existing.data = customer       # 무조건 최신화 (기존 데이터 덮어쓰기)
        updated += 1
    else:
        session.add(Customer(...))     # 새로 추가
```
주석도 "JSON이 source of truth이므로 DB에 없는 항목은 추가, 있는 항목은 최신화"로 스스로 밝히고 있어 이름과 의도가 어긋남.

**재현 시나리오**:
1. 운영자가 웹 UI(`POST /api/customers`)로 신규 고객 `C012` 추가.
2. 배포/리스타트 시 `lifespan`이 이 함수를 호출 → JSON에 없는 `C012`는 유지되지만, JSON에 있는 `C001~C011`은 운영 중에 웹으로 수정된 `data`가 **매 부팅마다 JSON 원본으로 덮이리는** 리스크.

실제로 `web/app.py:lifespan`은 PostgreSQL 경로에서 이 함수를 쓰지 않고 psycopg2 직접 upsert로 우회하고 있어(`app.py:82-113`) 이미 이 문제를 회피했지만, 로컬 SQLite 폴백 경로(`app.py:69`)는 여전히 이 함수를 호출.

**수정안**: 이름과 동작 둘 중 하나를 맞춘다.
- 이름 유지 + 동작 변경:
  ```python
  def seed_customers_if_empty() -> None:
      with _session() as session:
          if session.query(Customer).count() > 0: return
          ... # 빈 DB에만 삽입
  ```
- 또는 이름을 `sync_customers_from_json()`으로 변경해 호출부도 함께 rename.

---

*작성: 2026-04-24 · 기준 커밋 `cd89cb0`*
