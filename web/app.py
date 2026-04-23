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
from fastapi import FastAPI, Request, UploadFile, File
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
from agents.dislike_checker_agent import DislikeCheckerAgent


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
    "openai/gpt-oss-120b:free": {
        "label": "GPT-OSS 120B (무료)",
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
        "activities_updated_at": dt.get_activities_updated_at(customer_id),
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


class CustomerContact(BaseModel):
    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""


class CustomerCreate(BaseModel):
    customer_id: str = ""  # 비어있으면 서버가 자동 채번
    company_name: str
    company_type: str = ""
    aum_billion_krw: float = 0
    contact: CustomerContact = CustomerContact()
    investment_mandate: list[str] = []
    benchmark: str = ""
    relationship_since: str = ""
    tier: str = ""
    assigned_salesperson: str = ""


class CustomerDelete(BaseModel):
    customer_ids: list[str]


class SalesNoteDelete(BaseModel):
    note_ids: list[str]


class DislikeCheckRequest(BaseModel):
    note_ids: list[str]


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


@app.delete("/api/sales-notes")
async def api_delete_sales_notes(body: SalesNoteDelete):
    """영업 노트 일괄 삭제 — 연관 페르소나/NBA/Activity/QC는 건드리지 않음."""
    from fastapi import HTTPException
    ids = [nid for nid in body.note_ids if nid]
    if not ids:
        raise HTTPException(status_code=400, detail="삭제할 note_id가 없습니다.")
    return dt.delete_sales_notes(ids)


@app.post("/api/sales-notes/check-dislikes")
async def api_check_dislikes(body: DislikeCheckRequest):
    """선택된 세일즈 노트들의 Action_Point가 해당 고객 페르소나의 explicit_dislikes에
    해당하는지 DislikeCheckerAgent로 판정하고, 결과를 각 노트에 영속화한다.
    고객별로 그룹화해 에이전트를 1회씩 실행 (호출 비용 최소화)."""
    from fastapi import HTTPException

    ids = [nid for nid in body.note_ids if nid]
    if not ids:
        raise HTTPException(status_code=400, detail="분석할 note_id가 없습니다.")

    # 1) 노트 로드 및 유효성 체크 — note_id → (customer_id, action_point) 맵
    id_to_meta: dict[str, dict] = {}
    missing: list[str] = []
    for nid in ids:
        # sales_notes 테이블을 직접 조회 (data_tools에 note_id 단건 조회 헬퍼가 없어 경량 인라인)
        from db.database import SalesNote
        with dt._session() as session:
            row = session.query(SalesNote).filter_by(note_id=nid).first()
            if not row:
                missing.append(nid)
                continue
            data = row.data or {}
            id_to_meta[nid] = {
                "customer_id": row.customer_id,
                "action_point": data.get("Action_Point") or "",
            }

    # 2) 고객별 그룹화
    groups: dict[str, list[dict]] = {}
    for nid, meta in id_to_meta.items():
        groups.setdefault(meta["customer_id"], []).append({
            "note_id": nid,
            "action_point": meta["action_point"],
        })

    # 3) 모델/프로바이더 선택 — 전역 모델 설정 재사용
    selected_model = _model_setting["model"]
    provider = MODEL_REGISTRY.get(selected_model, MODEL_REGISTRY["claude-opus-4-6"])["provider"]

    aggregated: list[dict] = []
    skipped_customers: list[dict] = []
    checked_at = dt.now_kst_str()

    # 4) 고객별로 에이전트 실행
    for cid, notes in groups.items():
        customer = dt.get_customer(cid) or {}
        company_name = customer.get("company_name", cid)
        persona = dt.get_persona(cid) or {}
        dislikes = persona.get("explicit_dislikes") or []

        # Action_Point가 비어있는 노트는 LLM에 보내지 않고 즉시 false 처리
        empty_ap = [n for n in notes if not (n.get("action_point") or "").strip()]
        nonempty_ap = [n for n in notes if (n.get("action_point") or "").strip()]

        results_for_customer: list[dict] = [
            {
                "note_id": n["note_id"],
                "is_red_flag": False,
                "matched_dislike": "",
                "reason": "Action Point 없음",
            }
            for n in empty_ap
        ]

        if nonempty_ap:
            if not persona:
                skipped_customers.append({"customer_id": cid, "reason": "페르소나 미생성"})
                results_for_customer.extend([
                    {
                        "note_id": n["note_id"],
                        "is_red_flag": False,
                        "matched_dislike": "",
                        "reason": "페르소나 미생성 — 판정 불가",
                    }
                    for n in nonempty_ap
                ])
            elif not dislikes:
                results_for_customer.extend([
                    {
                        "note_id": n["note_id"],
                        "is_red_flag": False,
                        "matched_dislike": "",
                        "reason": "페르소나에 explicit_dislikes 항목 없음",
                    }
                    for n in nonempty_ap
                ])
            else:
                try:
                    agent = DislikeCheckerAgent(model=selected_model, provider=provider)
                    batch = agent.check(cid, company_name, list(dislikes), nonempty_ap)
                    results_for_customer.extend(batch)
                except Exception as exc:
                    import traceback; traceback.print_exc()
                    results_for_customer.extend([
                        {
                            "note_id": n["note_id"],
                            "is_red_flag": False,
                            "matched_dislike": "",
                            "reason": f"에이전트 오류: {type(exc).__name__}",
                        }
                        for n in nonempty_ap
                    ])

        # 5) 각 노트에 영속화
        for r in results_for_customer:
            patch = {
                "_red_flag": bool(r["is_red_flag"]),
                "_red_flag_matched": r.get("matched_dislike", ""),
                "_red_flag_reason": r.get("reason", ""),
                "_red_flag_checked_at": checked_at,
            }
            dt.update_sales_note(r["note_id"], patch)
            # 응답 정리
            aggregated.append({
                "note_id": r["note_id"],
                "customer_id": cid,
                "red_flag": bool(r["is_red_flag"]),
                "matched_dislike": r.get("matched_dislike", ""),
                "reason": r.get("reason", ""),
            })

    return {
        "checked_at": checked_at,
        "total": len(aggregated),
        "flagged": sum(1 for r in aggregated if r["red_flag"]),
        "missing": missing,
        "skipped_customers": skipped_customers,
        "results": aggregated,
    }


@app.post("/api/sales-notes/upload")
async def api_upload_sales_notes_csv(file: UploadFile = File(...)):
    """CSV 파일을 파싱하고 행별 검증 결과를 반환 (DB 저장 없음)."""
    import csv as _csv

    raw = await file.read()
    # 인코딩 자동 감지: utf-8-sig (BOM 포함) → cp949
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return JSONResponse(
            {"error": "CSV 인코딩을 인식할 수 없습니다 (UTF-8/CP949 지원)."},
            status_code=400,
        )

    try:
        reader = _csv.DictReader(io.StringIO(text))
        rows_raw = list(reader)
    except Exception as exc:
        return JSONResponse({"error": f"CSV 파싱 실패: {exc}"}, status_code=400)

    # Client_Name → customer_id 매핑
    customers = dt.get_all_customers()
    name_to_id = {c.get("company_name"): c.get("customer_id") for c in customers if c.get("company_name")}

    rows_out = []
    valid_cnt = 0
    for idx, row in enumerate(rows_raw):
        client_name = (row.get("Client_Name") or "").strip()
        activity_date = (row.get("Activity_Date") or "").strip()
        customer_id = name_to_id.get(client_name)

        error = None
        if not client_name:
            error = "Client_Name 누락"
        elif not customer_id:
            error = f"매칭되는 고객사 없음: {client_name}"
        elif not activity_date:
            error = "Activity_Date 누락"

        row_clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        row_clean.update({
            "_row_index": idx + 2,  # CSV 행 번호 (헤더=1, 데이터 시작=2)
            "_customer_id": customer_id,
            "_valid": error is None,
            "_error": error,
        })
        if error is None:
            valid_cnt += 1
        rows_out.append(row_clean)

    return {
        "rows": rows_out,
        "summary": {"total": len(rows_out), "valid": valid_cnt, "invalid": len(rows_out) - valid_cnt},
    }


class BulkCommitBody(BaseModel):
    rows: list[dict]


@app.post("/api/sales-notes/bulk-commit")
async def api_bulk_commit_sales_notes(body: BulkCommitBody):
    """upload에서 파싱된 rows 중 _valid=true 항목을 DB에 insert."""
    inserted = 0
    failed: list[dict] = []
    for row in body.rows:
        if not row.get("_valid"):
            continue
        customer_id = row.get("_customer_id")
        if not customer_id:
            failed.append({"row_index": row.get("_row_index"), "error": "customer_id 없음"})
            continue
        # 메타 필드 제거 후 순수 note 데이터만 저장
        note_data = {k: v for k, v in row.items() if not k.startswith("_")}
        try:
            dt.add_sales_note(customer_id, note_data)
            inserted += 1
        except Exception as exc:
            failed.append({"row_index": row.get("_row_index"), "error": str(exc)})
    return {"inserted": inserted, "failed": failed}


@app.get("/api/customers")
async def api_customers():
    return dt.get_all_customers()


@app.post("/api/customers")
async def api_create_customer(body: CustomerCreate):
    """신규 고객 생성. customer_id 비어 있으면 자동 채번."""
    from fastapi import HTTPException
    payload = body.model_dump()
    if not payload.get("company_name", "").strip():
        raise HTTPException(status_code=400, detail="회사명은 필수입니다.")
    try:
        return dt.create_customer(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.delete("/api/customers")
async def api_delete_customers(body: CustomerDelete):
    """고객과 연관 레코드(sales_notes/personas/nba/activities/qc) 일괄 삭제."""
    from fastapi import HTTPException
    ids = [cid for cid in body.customer_ids if cid]
    if not ids:
        raise HTTPException(status_code=400, detail="삭제할 customer_id가 없습니다.")
    return dt.delete_customers(ids)


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
                    _ts = dt.now_kst_str("%Y-%m-%d %H:%M:%S")
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
                    ts = dt.now_kst_str("%Y-%m-%d %H:%M:%S")
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


def _run_persona_all_thread(customers: list, force: bool, q: queue.Queue, model: str, provider: str) -> None:
    """전체 고객 페르소나를 순차적으로 업데이트하면서 진행 상황을 queue에 put."""
    total = len(customers)
    for i, c in enumerate(customers, 1):
        cid = c.get("customer_id", "")
        name = c.get("company_name", cid)
        try:
            if force:
                since_date = None
            else:
                existing = dt.get_persona(cid)
                since_date = existing.get("updated_at") if existing else None
            q.put({"type": "progress", "index": i, "total": total, "customer_id": cid, "company_name": name, "status": "started"})
            agent = PersonaAgent(model=model, provider=provider)
            agent.run(cid, since_date=since_date)
            q.put({"type": "progress", "index": i, "total": total, "customer_id": cid, "company_name": name, "status": "done"})
        except Exception as exc:
            import traceback; traceback.print_exc()
            q.put({"type": "progress", "index": i, "total": total, "customer_id": cid, "company_name": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    q.put(None)


@app.get("/api/run/persona-all")
async def api_run_persona_all(force: bool = False):
    """전체 고객 페르소나 일괄 업데이트 (SSE, 순차 실행).
    force=false: 각 고객의 마지막 updated_at 이후 노트만 반영 (증분)
    force=true : 전체 재생성"""
    if "persona-all" in running_set:
        async def _busy():
            yield f'data: {json.dumps({"type": "error", "text": "이미 전체 페르소나 업데이트가 진행 중입니다."})}\n\n'
        return StreamingResponse(_busy(), media_type="text/event-stream")

    customers = dt.get_all_customers() or []
    if not customers:
        async def _empty():
            yield f'data: {json.dumps({"type": "error", "text": "고객이 없습니다."})}\n\n'
        return StreamingResponse(_empty(), media_type="text/event-stream")

    selected = _model_setting["model"]
    meta = MODEL_REGISTRY.get(selected, MODEL_REGISTRY["claude-opus-4-6"])
    provider = meta["provider"]

    async def event_stream() -> AsyncGenerator[str, None]:
        q: queue.Queue = queue.Queue()
        running_set.add("persona-all")
        t = threading.Thread(
            target=_run_persona_all_thread,
            args=(customers, force, q, selected, provider),
            daemon=True,
        )
        t.start()

        succeeded, failed = 0, 0
        try:
            while True:
                try:
                    msg = q.get(timeout=0.1)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue

                if msg is None:
                    ts = dt.now_kst_str("%Y-%m-%d %H:%M:%S")
                    yield f'data: {json.dumps({"type": "done", "total": len(customers), "succeeded": succeeded, "failed": failed, "completed_at": ts}, ensure_ascii=False)}\n\n'
                    break

                if isinstance(msg, dict) and msg.get("type") == "progress":
                    status = msg.get("status")
                    if status == "done":
                        succeeded += 1
                    elif status == "error":
                        failed += 1
                    yield f'data: {json.dumps(msg, ensure_ascii=False)}\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False)}\n\n'
        finally:
            running_set.discard("persona-all")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── 진입점 ───────────────────────────────────────────────────────────────────

