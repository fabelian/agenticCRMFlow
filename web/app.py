"""
FastAPI 웹 애플리케이션 - CRM 멀티에이전트 시스템
"""

import json
import os
import queue
import sys
import threading
import io
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import urlparse

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# .env 로딩 (로컬 개발 전용)
# Railway 환경에서는 환경 변수가 직접 설정되므로 .env 파일이 없어도 정상 동작
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(env_file, override=False)  # 기존 환경 변수를 덮어쓰지 않음

# src/ 디렉토리를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.database import init_db
from tools import data_tools as dt
from agents.orchestrator import OrchestratorAgent
from agents.persona_agent import PersonaAgent
from agents.nba_agent import NBAAgent
from agents.activity_agent import ActivityAgent
from agents.qc_agent import QCAgent


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stdout,
        force=True,  # uvicorn이 이미 설정해도 덮어씀
    )
    print("[lifespan] START — DB 초기화 및 시드 실행 중...", flush=True)
    try:
        init_db()
        print("[lifespan] init_db() OK", flush=True)
    except Exception as e:
        print(f"[lifespan] init_db() FAILED: {e}", flush=True)
        import traceback; traceback.print_exc()
    # ── customers.json → PostgreSQL (psycopg2 직접 삽입) ─────────────────────
    print("[lifespan] seed_customers START", flush=True)
    try:
        import psycopg2

        db_url = os.environ.get("DATABASE_URL", "")
        print(f"[lifespan] DATABASE_URL present: {bool(db_url)}", flush=True)

        if not db_url:
            # 로컬 SQLite 모드: SQLAlchemy 기반 시드 사용
            dt.seed_customers_if_empty()
            print("[lifespan] seed_customers OK (SQLite fallback)", flush=True)
        else:
            # Railway PostgreSQL: psycopg2로 직접 삽입
            customers_path = Path(__file__).parent.parent / "data" / "customers.json"
            print(f"[lifespan] customers.json path: {customers_path} exists={customers_path.exists()}", flush=True)

            if not customers_path.exists():
                print("[lifespan] ERROR: customers.json not found!", flush=True)
            else:
                with open(customers_path, encoding="utf-8") as f:
                    customers_data = json.load(f)
                print(f"[lifespan] Loaded {len(customers_data)} customers from JSON", flush=True)

                conn = psycopg2.connect(db_url)
                conn.autocommit = False
                cur = conn.cursor()
                print("[lifespan] PostgreSQL connected", flush=True)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS customers (
                        customer_id VARCHAR PRIMARY KEY,
                        data        JSONB NOT NULL
                    )
                """)

                inserted = 0
                for customer in customers_data:
                    cid = customer.get("customer_id")
                    if not cid:
                        continue
                    cur.execute(
                        """
                        INSERT INTO customers (customer_id, data) VALUES (%s, %s)
                        ON CONFLICT (customer_id) DO UPDATE SET data = EXCLUDED.data
                        """,
                        (cid, json.dumps(customer, ensure_ascii=False)),
                    )
                    if cur.rowcount == 1:
                        inserted += 1

                conn.commit()
                cur.close()
                conn.close()
                print(f"[lifespan] seed_customers OK — {inserted}/{len(customers_data)} upserted", flush=True)

    except Exception as e:
        import traceback
        print(f"[lifespan] seed_customers FAILED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
    try:
        dt.seed_sales_notes_if_empty()
        print("[lifespan] seed_sales_notes OK", flush=True)
    except Exception as e:
        print(f"[lifespan] seed_sales_notes FAILED: {e}", flush=True)
        import traceback; traceback.print_exc()
    try:
        dt.seed_personas_if_empty()
        print("[lifespan] seed_personas OK", flush=True)
    except Exception as e:
        print(f"[lifespan] seed_personas FAILED: {e}", flush=True)
        import traceback; traceback.print_exc()
    print("[lifespan] DONE — 서버 준비됨", flush=True)
    yield

# data_tools._load를 공개 래퍼로 노출
def _load_json(filename: str):
    return dt._load(filename)

app = FastAPI(title="CRM 멀티에이전트 시스템", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ─── 모델 레지스트리 ──────────────────────────────────────────────────────────

MODEL_REGISTRY: dict[str, dict] = {
    "claude-opus-4-6": {
        "label": "Claude Opus 4.6",
        "provider": "anthropic",
        "description": "최고 성능 (기본값)",
    },
    "claude-sonnet-4-6": {
        "label": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "description": "빠른 속도 · 낮은 비용",
    },
    "google/gemma-4-26b-a4b-it:free": {
        "label": "Gemma 4 26B (무료)",
        "provider": "openrouter",
        "description": "OpenRouter 무료 — rate limit 있음",
    },
    "meta-llama/llama-4-scout:free": {
        "label": "Llama 4 Scout (무료)",
        "provider": "openrouter",
        "description": "OpenRouter 무료 — rate limit 있음",
    },
    "deepseek/deepseek-chat-v3-0324:free": {
        "label": "DeepSeek V3 (무료)",
        "provider": "openrouter",
        "description": "OpenRouter 무료 — rate limit 있음",
    },
    "minimax/minimax-m2.5:free": {
        "label": "MiniMax M2.5 (무료)",
        "provider": "openrouter",
        "description": "OpenRouter 무료 — rate limit 있음",
    },
}

_model_setting: dict[str, str] = {"model": "google/gemma-4-26b-a4b-it:free"}

# 현재 실행 중인 분석 고객 ID 집합 (중복 방지)
running_set: set[str] = set()


# ─── StreamCapture ────────────────────────────────────────────────────────────

class StreamCapture(io.TextIOBase):
    """sys.stdout을 가로채 queue에 넣는 IO 클래스"""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, s: str) -> int:
        if s.strip():
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


# ─── 파이프라인 실행 (별도 스레드) ───────────────────────────────────────────

def run_pipeline(customer_id: str, q: queue.Queue, model: str = "claude-opus-4-6", provider: str = "anthropic") -> None:
    old_stdout = sys.stdout
    sys.stdout = StreamCapture(q)
    try:
        orchestrator = OrchestratorAgent(model=model, provider=provider)
        orchestrator.run(customer_id)
    except Exception as e:
        q.put(f"[ERROR] {e}")
    finally:
        sys.stdout = old_stdout
        q.put(None)  # sentinel: 완료 신호


def run_single_agent(
    customer_id: str,
    agent_type: str,
    q: queue.Queue,
    model: str = "claude-opus-4-6",
    provider: str = "anthropic",
    since_date: str = None,
) -> None:
    """개별 에이전트를 단독 실행. agent_type: persona | nba | activity | qc"""
    old_stdout = sys.stdout
    sys.stdout = StreamCapture(q)
    try:
        if agent_type == "persona":
            agent = PersonaAgent(model=model, provider=provider)
            agent.run(customer_id, since_date=since_date)
        elif agent_type == "nba":
            agent = NBAAgent(model=model, provider=provider)
            agent.run(customer_id, since_date=since_date)
        elif agent_type == "activity":
            agent = ActivityAgent(model=model, provider=provider)
            agent.run(customer_id)
        elif agent_type == "qc":
            agent = QCAgent(model=model, provider=provider)
            agent.run(customer_id)
        else:
            q.put(f"[ERROR] 알 수 없는 에이전트 타입: {agent_type}")
    except Exception as e:
        import traceback
        q.put(f"[ERROR] {e}\n{traceback.format_exc()}")
    finally:
        sys.stdout = old_stdout
        q.put(None)


# ─── 헬퍼: 저장된 분석 결과 로드 ─────────────────────────────────────────────

def load_customer_results(customer_id: str) -> dict:
    """고객 기본 정보 + 저장된 분석 결과(persona, nba, activities, qc)를 반환"""
    customer = dt.get_customer(customer_id)
    if not customer:
        return {}

    return {
        "customer": customer,
        "persona": dt.get_persona(customer_id),
        "nba": dt.get_nba(customer_id),
        "activities": dt.get_activities(customer_id),
        "qc": dt.get_qc_report(customer_id),
    }


# ─── 페이지 라우트 ────────────────────────────────────────────────────────────

@app.get("/test")
async def test():
    return {"status": "ok", "message": "App is running"}


@app.get("/api/debug")
async def api_debug():
    """DB 상태 및 시드 현황 진단 엔드포인트"""
    import os
    from sqlalchemy import text, inspect as sa_inspect

    db_url = os.environ.get("DATABASE_URL", "")
    info = {
        "DATABASE_URL_set": bool(db_url),
        "DATABASE_URL_prefix": db_url[:40] if db_url else "(not set)",
        "tables": [],
        "row_counts": {},
        "customers_sample": [],
        "data_dir_exists": False,
        "customers_json_exists": False,
        "customers_json_count": 0,
    }

    # JSON 파일 확인
    from pathlib import Path
    data_dir = Path(__file__).parent.parent / "data"
    info["data_dir_exists"] = data_dir.exists()
    customers_json = data_dir / "customers.json"
    info["customers_json_exists"] = customers_json.exists()
    if customers_json.exists():
        import json as _json
        with open(customers_json, encoding="utf-8") as f:
            cdata = _json.load(f)
        info["customers_json_count"] = len(cdata)

    # DB 확인
    try:
        from db.database import engine
        inspector = sa_inspect(engine)
        info["tables"] = inspector.get_table_names()

        with engine.connect() as conn:
            for table in ["customers", "sales_notes", "personas", "nba_results", "activities", "qc_reports"]:
                if table in info["tables"]:
                    row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
                    info["row_counts"][table] = row[0] if row else 0

        # customers 샘플 조회
        from db.database import Customer
        with dt._session() as session:
            rows = session.query(Customer).limit(3).all()
            info["customers_sample"] = [r.data.get("company_name") for r in rows if r.data]

    except Exception as e:
        info["db_error"] = str(e)

    return info


@app.get("/api/debug/env")
async def api_debug_env():
    """DATABASE_URL 및 Railway 환경 변수 진단 (보안 마스킹 적용)"""
    raw_url = os.environ.get("DATABASE_URL", "")
    is_set = bool(raw_url)

    masked_url = None
    if is_set:
        masked_url = raw_url[:30] + "..." + raw_url[-10:] if len(raw_url) > 40 else raw_url[:30] + "..."

    db_host = db_port = db_user = db_name = parse_error = None
    if is_set:
        try:
            parsed = urlparse(raw_url)
            db_host = parsed.hostname
            db_port = parsed.port
            db_user = parsed.username
            db_name = parsed.path.lstrip("/") if parsed.path else None
        except Exception as exc:
            parse_error = str(exc)

    other_env = {k: os.environ.get(k) for k in [
        "RAILWAY_ENVIRONMENT", "RAILWAY_SERVICE_NAME", "RAILWAY_PROJECT_NAME",
        "RAILWAY_DEPLOYMENT_ID", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE",
        "PORT", "PYTHONPATH",
    ]}

    return JSONResponse({
        "database_url": {
            "is_set": is_set,
            "masked_value": masked_url,
            "scheme": urlparse(raw_url).scheme if is_set else None,
        },
        "parsed_connection": {
            "host": db_host, "port": db_port,
            "user": db_user, "database": db_name,
            "parse_error": parse_error,
        },
        "other_env": other_env,
    })


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        customers = dt.get_all_customers()
        if isinstance(customers, dict):
            customers = list(customers.values()) if customers else []
        elif not isinstance(customers, list):
            customers = []
        customers = [c for c in customers if isinstance(c, dict)]
    except Exception:
        customers = []

    try:
        personas = dt.get_all_personas()
        analyzed_ids = []
        if isinstance(personas, list):
            for p in personas:
                if isinstance(p, dict) and "customer_id" in p:
                    cid = p.get("customer_id")
                    if isinstance(cid, str):
                        analyzed_ids.append(cid)
    except Exception:
        analyzed_ids = []

    notes_info: dict[str, dict] = {}
    for c in customers:
        cid = c.get("customer_id")
        if cid:
            notes = dt.get_sales_notes(cid)
            notes_info[cid] = {
                "count": len(notes),
                "last_date": (notes[0].get("Activity_Date") or notes[0].get("date", "")) if notes else "",
            }

    try:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "customers": json.loads(json.dumps(customers)),
                "analyzed_ids": json.loads(json.dumps(analyzed_ids)),
                "notes_info": json.loads(json.dumps(notes_info)),
            },
        )
    except Exception as e:
        import traceback
        return HTMLResponse(
            f"<pre>Template rendering error:\n{traceback.format_exc()}</pre>",
            status_code=500,
        )


@app.get("/customer/{customer_id}", response_class=HTMLResponse)
async def customer_page(request: Request, customer_id: str):
    customer = dt.get_customer(customer_id)
    if not customer:
        return HTMLResponse("<h1>고객을 찾을 수 없습니다.</h1>", status_code=404)

    return templates.TemplateResponse(
        request,
        "customer.html",
        {
            "customer": customer,
            "customer_id": customer_id,
        },
    )


# ─── API 라우트 ───────────────────────────────────────────────────────────────

class ModelSelect(BaseModel):
    model: str


class SalesNoteCreate(BaseModel):
    customer_id: str
    Sales_Name: str
    Activity_Date: str
    Client_Type: str
    Client_Name: str
    Contact_Role: str = ""
    Contact_Name: str = ""
    Sector: str = ""
    Activity_Type: str
    Activity_Log: str
    Customer_Feedback: str = ""
    Action_Point: str = ""
    Language: str = "KR"


@app.get("/api/models")
async def api_models():
    """사용 가능한 모델 목록과 현재 선택 반환"""
    openrouter_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    openrouter_available = bool(openrouter_key and not openrouter_key.startswith("sk-or-..."))
    return {
        "current": _model_setting["model"],
        "openrouter_available": openrouter_available,
        "models": [
            {"id": mid, **meta}
            for mid, meta in MODEL_REGISTRY.items()
        ],
    }


@app.post("/api/model")
async def api_set_model(body: ModelSelect):
    """현재 사용 모델 변경"""
    if body.model not in MODEL_REGISTRY:
        return JSONResponse({"error": f"지원하지 않는 모델: {body.model}"}, status_code=400)
    meta = MODEL_REGISTRY[body.model]
    if meta["provider"] == "openrouter":
        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key or key.startswith("sk-or-..."):
            return JSONResponse(
                {"error": "OPENROUTER_API_KEY가 설정되지 않았습니다. Railway 환경변수에 유효한 키를 추가하세요."},
                status_code=400,
            )
    _model_setting["model"] = body.model
    return {"selected": body.model, "label": meta["label"], "provider": meta["provider"]}


@app.get("/api/sales-notes/{customer_id}")
async def api_get_sales_notes(customer_id: str):
    return dt.get_sales_notes(customer_id)


@app.post("/api/sales-notes")
async def api_add_sales_note(body: SalesNoteCreate):
    from fastapi import HTTPException
    customer = dt.get_customer(body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    note_data = body.model_dump()
    # Sales_ID 자동 생성 (기존 노트 수 기준)
    existing = dt.get_sales_notes(body.customer_id)
    note_data["Sales_ID"] = f"S{len(existing) + 1:02d}"
    return dt.add_sales_note(body.customer_id, note_data)


@app.get("/api/customers")
async def api_customers():
    return dt.get_all_customers()


@app.get("/api/all-sales-notes")
async def api_all_sales_notes():
    """전체 고객의 영업 노트를 통합 조회 (날짜 내림차순)"""
    customers = dt.get_all_customers()
    cid_to_name = {c["customer_id"]: c.get("company_name", c["customer_id"]) for c in customers if "customer_id" in c}
    all_notes = []
    for cid, name in cid_to_name.items():
        for n in dt.get_sales_notes(cid):
            n["_customer_id"] = cid
            n["_customer_name"] = name
            all_notes.append(n)
    all_notes.sort(key=lambda x: x.get("Activity_Date") or x.get("date", ""), reverse=True)
    return all_notes


@app.get("/api/all-personas")
async def api_all_personas():
    """전체 고객 페르소나 조회 (고객 정보 포함)"""
    personas = dt.get_all_personas()
    customers = {c["customer_id"]: c for c in dt.get_all_customers() if "customer_id" in c}
    for p in personas:
        cid = p.get("customer_id")
        if cid and cid in customers:
            p["_company_name"] = customers[cid].get("company_name", cid)
            p["_tier"] = customers[cid].get("tier", "")
    return personas


@app.get("/api/customer/{customer_id}")
async def api_customer(customer_id: str):
    result = load_customer_results(customer_id)
    if not result:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return result


@app.get("/api/analyze/{customer_id}")
async def api_analyze(customer_id: str):
    """SSE 스트리밍으로 파이프라인 실행 결과를 실시간 전달"""

    if customer_id in running_set:
        async def already_running():
            yield 'data: {"type": "error", "text": "이미 분석이 진행 중입니다."}\n\n'
        return StreamingResponse(already_running(), media_type="text/event-stream")

    async def event_stream() -> AsyncGenerator[str, None]:
        q: queue.Queue = queue.Queue()
        running_set.add(customer_id)

        selected = _model_setting["model"]
        meta = MODEL_REGISTRY.get(selected, MODEL_REGISTRY["claude-opus-4-6"])
        thread = threading.Thread(
            target=run_pipeline,
            args=(customer_id, q, selected, meta["provider"]),
            daemon=True,
        )
        thread.start()

        try:
            while True:
                try:
                    msg = q.get(timeout=0.1)
                except queue.Empty:
                    # 클라이언트에 heartbeat (연결 유지)
                    yield ": heartbeat\n\n"
                    continue

                if msg is None:
                    # 파이프라인 완료
                    from datetime import datetime as _dt
                    _ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                    yield f'data: {json.dumps({"type": "done", "completed_at": _ts})}\n\n'
                    break

                if isinstance(msg, str) and msg.startswith("[ERROR]"):
                    error_text = msg[7:].strip()
                    payload = json.dumps({"type": "error", "text": error_text}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    break

                # 일반 로그 메시지
                payload = json.dumps({"type": "log", "text": msg}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

        except Exception as e:
            payload = json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        finally:
            running_set.discard(customer_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── 개별 에이전트 SSE 헬퍼 ──────────────────────────────────────────────────

def _agent_sse(customer_id: str, agent_type: str, since_date: str = None):
    key = f"{customer_id}:{agent_type}"
    if key in running_set:
        async def _busy():
            yield f'data: {json.dumps({"type": "error", "text": "이미 실행 중입니다."})}\n\n'
        return StreamingResponse(_busy(), media_type="text/event-stream")

    async def _stream() -> AsyncGenerator[str, None]:
        q: queue.Queue = queue.Queue()
        running_set.add(key)
        selected = _model_setting["model"]
        meta = MODEL_REGISTRY.get(selected, MODEL_REGISTRY["claude-opus-4-6"])
        t = threading.Thread(
            target=run_single_agent,
            args=(customer_id, agent_type, q, selected, meta["provider"], since_date),
            daemon=True,
        )
        t.start()
        try:
            while True:
                try:
                    msg = q.get(timeout=0.1)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                if msg is None:
                    from datetime import datetime as _dt
                    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                    yield f'data: {json.dumps({"type": "done", "completed_at": ts})}\n\n'
                    break
                if isinstance(msg, str) and msg.startswith("[ERROR]"):
                    yield f'data: {json.dumps({"type": "error", "text": msg[7:].strip()})}\n\n'
                    break
                yield f'data: {json.dumps({"type": "log", "text": msg}, ensure_ascii=False)}\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"type": "error", "text": str(e)})}\n\n'
        finally:
            running_set.discard(key)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/run/persona/{customer_id}")
async def api_run_persona(customer_id: str, force: bool = False):
    """Persona Agent 단독 실행 — 기본은 증분, force=true 시 전체 재생성"""
    if force:
        since_date = None
    else:
        persona = dt.get_persona(customer_id)
        since_date = persona.get("updated_at") if persona else None
    return _agent_sse(customer_id, "persona", since_date)


@app.get("/api/run/nba/{customer_id}")
async def api_run_nba(customer_id: str, force: bool = False):
    """NBA Agent 단독 실행 — 기본은 증분, force=true 시 전체 재생성"""
    if force:
        since_date = None
    else:
        nba = dt.get_nba(customer_id)
        since_date = nba.get("generated_at") if nba else None
    return _agent_sse(customer_id, "nba", since_date)


@app.get("/api/run/activity/{customer_id}")
async def api_run_activity(customer_id: str):
    """Activity Agent 단독 실행 — 최신 NBA 결과 기반"""
    return _agent_sse(customer_id, "activity")


@app.get("/api/run/qc/{customer_id}")
async def api_run_qc(customer_id: str):
    """QC Agent 단독 실행 — 모든 에이전트 최신 결과 검수"""
    return _agent_sse(customer_id, "qc")


# ─── 진입점 ───────────────────────────────────────────────────────────────────

