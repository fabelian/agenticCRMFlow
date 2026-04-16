"""
FastAPI 웹 애플리케이션 - CRM 멀티에이전트 시스템
"""

import json
import queue
import sys
import threading
import io
from pathlib import Path
from typing import AsyncGenerator

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
    _logging.info("[lifespan] 애플리케이션 시작 — DB 초기화 및 시드 실행 중...")
    init_db()
    _logging.info("[lifespan] init_db() 완료")
    dt.seed_customers_if_empty()
    _logging.info("[lifespan] seed_customers_if_empty() 완료")
    dt.seed_sales_notes_if_empty()
    _logging.info("[lifespan] seed_sales_notes_if_empty() 완료")
    dt.seed_personas_if_empty()
    _logging.info("[lifespan] seed_personas_if_empty() 완료 — 서버 준비됨")
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

_model_setting: dict[str, str] = {"model": "claude-opus-4-6"}

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
    date: str
    author: str
    channel: str
    title: str
    content: str
    sentiment: str
    key_concerns: list[str] = []
    expressed_interests: list[str] = []
    follow_up_required: bool = False


@app.get("/api/models")
async def api_models():
    """사용 가능한 모델 목록과 현재 선택 반환"""
    return {
        "current": _model_setting["model"],
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
    _model_setting["model"] = body.model
    meta = MODEL_REGISTRY[body.model]
    return {"selected": body.model, "label": meta["label"], "provider": meta["provider"]}


@app.get("/api/sales-notes/{customer_id}")
async def api_get_sales_notes(customer_id: str):
    return dt.get_sales_notes(customer_id)


@app.post("/api/sales-notes")
async def api_add_sales_note(body: SalesNoteCreate):
    customer = dt.get_customer(body.customer_id)
    if not customer:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return dt.add_sales_note(body.customer_id, body.model_dump())


@app.get("/api/customers")
async def api_customers():
    return dt.get_all_customers()


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
async def api_run_persona(customer_id: str):
    """Persona Agent 단독 실행 — 마지막 페르소나 업데이트 이후 노트만 사용"""
    persona = dt.get_persona(customer_id)
    since_date = persona.get("updated_at") if persona else None
    return _agent_sse(customer_id, "persona", since_date)


@app.get("/api/run/nba/{customer_id}")
async def api_run_nba(customer_id: str):
    """NBA Agent 단독 실행 — 마지막 NBA 제안 이후 노트만 사용"""
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
