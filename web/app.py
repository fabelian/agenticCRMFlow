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
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# .env 로딩 (프로젝트 루트)
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# src/ 디렉토리를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.database import init_db
from tools import data_tools as dt
from agents.orchestrator import OrchestratorAgent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

# data_tools._load를 공개 래퍼로 노출
def _load_json(filename: str):
    return dt._load(filename)

app = FastAPI(title="CRM 멀티에이전트 시스템", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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

def run_pipeline(customer_id: str, q: queue.Queue) -> None:
    old_stdout = sys.stdout
    sys.stdout = StreamCapture(q)
    try:
        orchestrator = OrchestratorAgent()
        orchestrator.run(customer_id)
    except Exception as e:
        q.put(f"[ERROR] {e}")
    finally:
        sys.stdout = old_stdout
        q.put(None)  # sentinel: 완료 신호


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

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    customers = dt.get_all_customers()
    try:
        personas = dt.get_all_personas()
        analyzed_ids = [p.get("customer_id") for p in personas if p.get("customer_id")]
    except Exception:
        analyzed_ids = []

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "customers": customers,
            "analyzed_ids": analyzed_ids,
        },
    )


@app.get("/customer/{customer_id}", response_class=HTMLResponse)
async def customer_page(request: Request, customer_id: str):
    customer = dt.get_customer(customer_id)
    if not customer:
        return HTMLResponse("<h1>고객을 찾을 수 없습니다.</h1>", status_code=404)

    return templates.TemplateResponse(
        "customer.html",
        {
            "request": request,
            "customer": customer,
            "customer_id": customer_id,
        },
    )


# ─── API 라우트 ───────────────────────────────────────────────────────────────

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

        thread = threading.Thread(
            target=run_pipeline,
            args=(customer_id, q),
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
                    yield 'data: {"type": "done"}\n\n'
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


# ─── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
