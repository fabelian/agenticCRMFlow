"""
CRM 데이터 액세스 레이어
- 정적 데이터 (customers, sales_notes, action_plans): JSON 파일 읽기 전용
- 에이전트 출력 (personas, nba_results, activities, qc_reports): DB (SQLite/PostgreSQL)
"""

import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def _load(filename: str) -> list | dict:
    """정적 JSON 파일 읽기 (customers, sales_notes, action_plans 전용)"""
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _session():
    from db.database import SessionLocal
    return SessionLocal()


# ─── 원본 데이터 조회 (JSON 파일, 읽기 전용) ──────────────────────────────────

def get_customer(customer_id: str) -> dict | None:
    customers = _load("customers.json")
    return next((c for c in customers if c["customer_id"] == customer_id), None)


def get_all_customers() -> list:
    return _load("customers.json")


def get_sales_notes(customer_id: str) -> list:
    """영업 노트 조회 (DB 우선, 실패 시 JSON fallback)"""
    try:
        from db.database import SalesNote
        with _session() as session:
            rows = session.query(SalesNote).filter_by(customer_id=customer_id).all()
            notes = [row.data for row in rows]
            return sorted(notes, key=lambda x: x.get("date", ""), reverse=True)
    except Exception:
        notes = _load("sales_notes.json")
        result = [n for n in notes if n["customer_id"] == customer_id]
        return sorted(result, key=lambda x: x["date"], reverse=True)


def add_sales_note(customer_id: str, note_data: dict) -> dict:
    """새 영업 노트 DB 저장 (note_id 자동 생성)"""
    from db.database import SalesNote
    with _session() as session:
        rows = session.query(SalesNote).filter_by(customer_id=customer_id).all()
        nums = []
        for row in rows:
            try:
                nums.append(int(row.note_id.split("-")[-1]))
            except (ValueError, IndexError):
                pass
        next_n = (max(nums) + 1) if nums else 1
        note_id = f"SN-{customer_id}-{next_n:03d}"
        note_data["note_id"] = note_id
        note_data["customer_id"] = customer_id
        session.add(SalesNote(note_id=note_id, customer_id=customer_id, data=note_data))
        session.commit()
    return note_data


def seed_sales_notes_if_empty() -> None:
    """JSON → DB 초기 이전 (DB가 비어있을 때만 실행)"""
    try:
        from db.database import SalesNote
        with _session() as session:
            if session.query(SalesNote).count() == 0:
                for note in _load("sales_notes.json"):
                    session.add(SalesNote(
                        note_id=note["note_id"],
                        customer_id=note["customer_id"],
                        data=note,
                    ))
                session.commit()
    except Exception:
        pass


def seed_personas_if_empty() -> None:
    """data/personas.json → DB 이전 (customer_id 기준으로 없는 것만 삽입)"""
    try:
        from db.database import Persona
        with _session() as session:
            for persona in _load("personas.json"):
                cid = persona.get("customer_id")
                if cid and not session.query(Persona).filter_by(customer_id=cid).first():
                    session.add(Persona(customer_id=cid, data=persona))
            session.commit()
    except Exception:
        pass


def get_action_plans(customer_id: str) -> list:
    plans = _load("action_plans.json")
    result = [p for p in plans if p["customer_id"] == customer_id]
    return sorted(result, key=lambda x: x["created_date"], reverse=True)


def get_pending_actions(customer_id: str) -> list:
    """미완료 Action Item만 추출"""
    plans = get_action_plans(customer_id)
    pending = []
    for plan in plans:
        for action in plan.get("actions", []):
            if action["status"] != "완료":
                pending.append({
                    "plan_id": plan["plan_id"],
                    "plan_title": plan["title"],
                    "action": action["action"],
                    "due": action["due"],
                    "status": action["status"],
                })
    return pending


# ─── 페르소나 관리 (DB) ───────────────────────────────────────────────────────

def save_persona(customer_id: str, persona: dict) -> None:
    from db.database import Persona, flag_modified
    persona["customer_id"] = customer_id
    persona["updated_at"] = datetime.now().strftime("%Y-%m-%d")
    with _session() as session:
        existing = session.query(Persona).filter_by(customer_id=customer_id).first()
        if existing:
            existing.data = persona
            flag_modified(existing, "data")
        else:
            session.add(Persona(customer_id=customer_id, data=persona))
        session.commit()


def get_persona(customer_id: str) -> dict | None:
    try:
        from db.database import Persona
        with _session() as session:
            row = session.query(Persona).filter_by(customer_id=customer_id).first()
            return row.data if row else None
    except Exception:
        return None


def get_all_personas() -> list:
    try:
        from db.database import Persona
        with _session() as session:
            return [row.data for row in session.query(Persona).all()]
    except Exception:
        return []


# ─── NBA 추천 관리 (DB) ───────────────────────────────────────────────────────

def save_nba(customer_id: str, nba_data: dict) -> None:
    from db.database import NBAResult, flag_modified
    nba_data["customer_id"] = customer_id
    nba_data["generated_at"] = datetime.now().strftime("%Y-%m-%d")
    with _session() as session:
        existing = session.query(NBAResult).filter_by(customer_id=customer_id).first()
        if existing:
            existing.data = nba_data
            flag_modified(existing, "data")
        else:
            session.add(NBAResult(customer_id=customer_id, data=nba_data))
        session.commit()


def get_nba(customer_id: str) -> dict | None:
    try:
        from db.database import NBAResult
        with _session() as session:
            row = session.query(NBAResult).filter_by(customer_id=customer_id).first()
            return row.data if row else None
    except Exception:
        return None


# ─── 활동 일정 관리 (DB) ──────────────────────────────────────────────────────

def save_activities(customer_id: str, activities: list) -> None:
    from db.database import ActivitySchedule, flag_modified
    with _session() as session:
        existing = session.query(ActivitySchedule).filter_by(customer_id=customer_id).first()
        if existing:
            existing.data = activities
            flag_modified(existing, "data")
        else:
            session.add(ActivitySchedule(customer_id=customer_id, data=activities))
        session.commit()


def get_activities(customer_id: str) -> list:
    try:
        from db.database import ActivitySchedule
        with _session() as session:
            row = session.query(ActivitySchedule).filter_by(customer_id=customer_id).first()
            return row.data if row else []
    except Exception:
        return []


# ─── QC 보고서 관리 (DB) ──────────────────────────────────────────────────────

def save_qc_report(customer_id: str, report: dict) -> None:
    from db.database import QCReport, flag_modified
    report["customer_id"] = customer_id
    report["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _session() as session:
        existing = session.query(QCReport).filter_by(customer_id=customer_id).first()
        if existing:
            existing.data = report
            flag_modified(existing, "data")
        else:
            session.add(QCReport(customer_id=customer_id, data=report))
        session.commit()


def get_qc_report(customer_id: str) -> dict | None:
    try:
        from db.database import QCReport
        with _session() as session:
            row = session.query(QCReport).filter_by(customer_id=customer_id).first()
            return row.data if row else None
    except Exception:
        return None


# ─── 전체 컨텍스트 조합 ───────────────────────────────────────────────────────

def build_raw_context(customer_id: str) -> dict:
    """에이전트에게 전달할 원본 데이터 전체 조합"""
    return {
        "customer": get_customer(customer_id),
        "sales_notes": get_sales_notes(customer_id),
        "action_plans": get_action_plans(customer_id),
        "pending_actions": get_pending_actions(customer_id),
    }


def build_full_context(customer_id: str) -> dict:
    """에이전트 결과물까지 포함한 전체 컨텍스트"""
    return {
        **build_raw_context(customer_id),
        "persona": get_persona(customer_id),
        "nba": get_nba(customer_id),
        "activities": get_activities(customer_id),
        "qc_report": get_qc_report(customer_id),
    }
