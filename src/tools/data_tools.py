"""
CRM 데이터 액세스 레이어
- 정적 데이터 (customers, sales_notes, action_plans): JSON 파일 읽기 전용
- 에이전트 출력 (personas, nba_results, activities, qc_reports): DB (SQLite/PostgreSQL)
"""

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

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

def seed_customers_if_empty() -> None:
    """data/customers.json → DB 동기화 (시작 시 항상 upsert).
    JSON이 source of truth이므로 DB에 없는 항목은 추가, 있는 항목은 최신화."""
    import os
    from sqlalchemy import text
    from sqlalchemy import inspect as sa_inspect

    db_url = os.environ.get("DATABASE_URL", "")
    print(f"[seed_customers] START — DATABASE_URL prefix: {db_url[:30] if db_url else '(not set)'}", flush=True)
    logger.info("seed_customers_if_empty: starting. DATABASE_URL prefix: %s",
                db_url[:30] if db_url else "(not set)")

    try:
        from db.database import engine, Customer, flag_modified
    except Exception:
        logger.error("seed_customers_if_empty: failed to import engine", exc_info=True)
        return

    # 1. DB 연결 확인
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[seed_customers] DB connection OK", flush=True)
        logger.info("seed_customers_if_empty: DB connection OK")
    except Exception as e:
        print(f"[seed_customers] DB connection FAILED: {e}", flush=True)
        logger.error("seed_customers_if_empty: DB connection FAILED", exc_info=True)
        return

    # 2. customers 테이블 존재 확인
    try:
        tables = sa_inspect(engine).get_table_names()
        print(f"[seed_customers] existing tables: {tables}", flush=True)
        logger.info("seed_customers_if_empty: existing tables: %s", tables)
        if "customers" not in tables:
            print("[seed_customers] 'customers' table missing — running init_db()", flush=True)
            from db.database import init_db
            init_db()
    except Exception as e:
        print(f"[seed_customers] table inspection FAILED: {e}", flush=True)
        logger.error("seed_customers_if_empty: table inspection failed", exc_info=True)
        return

    # 3. upsert (항상 최신 JSON 반영)
    try:
        with _session() as session:
            customers = _load("customers.json")
            print(f"[seed_customers] loaded {len(customers)} customers from JSON", flush=True)
            logger.info("seed_customers_if_empty: loaded %d customers from JSON", len(customers))
            added, updated = 0, 0
            for customer in customers:
                cid = customer.get("customer_id")
                if not cid:
                    continue
                existing = session.query(Customer).filter_by(customer_id=cid).first()
                if existing:
                    existing.data = customer
                    flag_modified(existing, "data")
                    updated += 1
                else:
                    session.add(Customer(customer_id=cid, data=customer))
                    added += 1
            session.commit()
            print(f"[seed_customers] DONE — added={added} updated={updated}", flush=True)
            logger.info("seed_customers_if_empty: done — added=%d updated=%d", added, updated)
    except Exception as e:
        print(f"[seed_customers] upsert FAILED: {e}", flush=True)
        logger.error("seed_customers_if_empty: upsert failed", exc_info=True)


def get_customer(customer_id: str) -> dict | None:
    """고객 조회 (DB 우선, 실패 시 JSON fallback)"""
    try:
        from db.database import Customer
        with _session() as session:
            row = session.query(Customer).filter_by(customer_id=customer_id).first()
            if row:
                return row.data
    except Exception as exc:
        logger.error("get_customer: DB query failed for %s (%s), falling back to JSON", customer_id, exc, exc_info=True)
    customers = _load("customers.json")
    return next((c for c in customers if c["customer_id"] == customer_id), None)


def get_all_customers() -> list:
    """전체 고객 조회 (DB 우선, 실패 시 JSON fallback)"""
    logger.info("get_all_customers: querying customers table in DB")
    try:
        from db.database import Customer
        with _session() as session:
            rows = session.query(Customer).all()
            if rows:
                customers = [row.data for row in rows]
                logger.info("get_all_customers: returned %d customers from DB", len(customers))
                return customers
            logger.warning("get_all_customers: customers table is empty, falling back to JSON")
    except Exception as exc:
        logger.error("get_all_customers: DB query failed (%s), falling back to JSON", exc, exc_info=True)
    customers = _load("customers.json")
    logger.info("get_all_customers: returned %d customers from JSON fallback", len(customers))
    return customers


def get_sales_notes(customer_id: str) -> list:
    """영업 노트 조회 (DB 우선, 빈 결과면 JSON에서 Client_Name으로 보완)"""
    try:
        from db.database import SalesNote
        with _session() as session:
            rows = session.query(SalesNote).filter_by(customer_id=customer_id).all()
            notes = [row.data for row in rows]
            if notes:
                return sorted(notes, key=lambda x: x.get("Activity_Date") or x.get("date", ""), reverse=True)
    except Exception:
        pass

    # JSON fallback: old schema(customer_id) 또는 new schema(Client_Name) 모두 지원
    customer = get_customer(customer_id)
    company_name = customer.get("company_name") if customer else None

    all_notes = _load("sales_notes.json")
    result = []
    for n in all_notes:
        if n.get("customer_id") == customer_id:
            result.append(n)
        elif company_name and n.get("Client_Name") == company_name:
            result.append(n)

    date_key = lambda x: x.get("Activity_Date") or x.get("date", "")
    return sorted(result, key=date_key, reverse=True)


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
    """JSON → DB 초기 이전 (DB가 비어있을 때만 실행).
    구 스키마(customer_id/note_id 보유)와 새 스키마(Client_Name 기반) 모두 지원."""
    logger.info("seed_sales_notes_if_empty: starting")
    try:
        from db.database import SalesNote
        with _session() as session:
            existing = session.query(SalesNote).count()
            if existing > 0:
                logger.info("seed_sales_notes_if_empty: %d notes already in DB, skipping", existing)
                return

            # company_name → customer_id 역 매핑
            customers = _load("customers.json")
            name_to_id = {c["company_name"]: c["customer_id"] for c in customers}
            logger.info("seed_sales_notes_if_empty: name_to_id map has %d entries", len(name_to_id))

            # 고객별 노트 카운터 (note_id 생성용)
            cust_counter: dict[str, int] = {}
            added, skipped = 0, 0

            for note in _load("sales_notes.json"):
                if "customer_id" in note and "note_id" in note:
                    # 구 스키마
                    session.add(SalesNote(
                        note_id=note["note_id"],
                        customer_id=note["customer_id"],
                        data=note,
                    ))
                    added += 1
                elif "Client_Name" in note:
                    # 새 스키마: Client_Name으로 customer_id 조회
                    customer_id = name_to_id.get(note["Client_Name"])
                    if not customer_id:
                        logger.warning("seed_sales_notes_if_empty: no customer_id for Client_Name=%s", note.get("Client_Name"))
                        skipped += 1
                        continue
                    cust_counter[customer_id] = cust_counter.get(customer_id, 0) + 1
                    note_id = f"SN-{customer_id}-{cust_counter[customer_id]:03d}"
                    note_with_ids = {**note, "note_id": note_id, "customer_id": customer_id}
                    session.add(SalesNote(
                        note_id=note_id,
                        customer_id=customer_id,
                        data=note_with_ids,
                    ))
                    added += 1
                else:
                    skipped += 1
            session.commit()
            logger.info("seed_sales_notes_if_empty: done — added=%d skipped=%d", added, skipped)
    except Exception:
        logger.error("seed_sales_notes_if_empty: failed", exc_info=True)


def seed_personas_if_empty() -> None:
    """data/personas.json → DB 이전 (customer_id 기준으로 없는 것만 삽입)"""
    logger.info("seed_personas_if_empty: starting")
    personas_data = _load("personas.json")
    if not personas_data:
        logger.info("seed_personas_if_empty: personas.json is empty or missing, skipping")
        return
    try:
        from db.database import Persona
        with _session() as session:
            added = 0
            for persona in personas_data:
                cid = persona.get("customer_id")
                if not cid:
                    continue
                if not session.query(Persona).filter_by(customer_id=cid).first():
                    session.add(Persona(customer_id=cid, data=persona))
                    added += 1
            session.commit()
            logger.info("seed_personas_if_empty: done — added=%d", added)
    except Exception:
        logger.error("seed_personas_if_empty: failed", exc_info=True)


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

def get_recent_notes_with_weights(customer_id: str, analysis_date: str = None, months: int = 3, since_date: str = None) -> dict:
    """분석일 기준 최근 N개월 세일즈 노트를 로드하고 recency_weight 부여.
    since_date(YYYY-MM-DD)가 지정되면 해당 날짜 이후 노트만 포함 (months 기반 cutoff 대체).
    가장 최근 노트일수록 weight 1.0, 오래될수록 낮아짐 (최솟값 0.1).
    새 스키마(Customer_Feedback 키)와 구 스키마(customer_id 기반) 모두 지원."""
    from datetime import datetime, timedelta

    if analysis_date:
        today = datetime.strptime(analysis_date, "%Y-%m-%d")
    else:
        today = datetime.today()

    if since_date:
        try:
            cutoff = datetime.strptime(since_date, "%Y-%m-%d")
        except ValueError:
            cutoff = today - timedelta(days=months * 30)
    else:
        cutoff = today - timedelta(days=months * 30)

    # 해당 고객의 모든 노트 (구/새 스키마 통합, DB → JSON fallback)
    all_customer_notes = get_sales_notes(customer_id)

    combined = []
    for note in all_customer_notes:
        date_str = note.get("Activity_Date") or note.get("date", "")
        try:
            note_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if note_date < cutoff:
            continue
        days_ago = (today - note_date).days
        weight = round(max(0.1, 1.0 - days_ago / (months * 30) * 0.9), 2)
        combined.append({
            "note_id": note.get("note_id", ""),
            "activity_date": date_str,
            "sales_id": note.get("Sales_ID") or note.get("author", ""),
            "sales_name": note.get("Sales_Name") or note.get("author", ""),
            "client_name": note.get("Client_Name") or customer_id,
            "sector": note.get("Sector", ""),
            "activity_type": note.get("Activity_Type") or note.get("channel", ""),
            "action_point": note.get("Action_Point") or note.get("content", ""),
            "customer_feedback": note.get("Customer_Feedback") or note.get("content", ""),
            "recency_weight": weight,
            "days_ago": days_ago,
        })

    combined.sort(key=lambda x: x["activity_date"], reverse=True)
    most_recent = combined[0] if combined else None

    return {
        "customer_id": customer_id,
        "analysis_date": today.strftime("%Y-%m-%d"),
        "note_count": len(combined),
        "most_recent_note": most_recent,
        "notes": combined,
    }


def get_customer_feedback_only(customer_id: str, since_date: str = None) -> dict:
    """세일즈 노트에서 Customer_Feedback 필드만 추출하여 반환.
    새 스키마(Customer_Feedback 키)와 구 스키마(customer_id 기반) 모두 지원.
    since_date(YYYY-MM-DD)가 지정되면 그 이후 노트만 포함."""
    from datetime import datetime
    since_dt = None
    if since_date:
        try:
            since_dt = datetime.strptime(since_date, "%Y-%m-%d")
        except ValueError:
            pass

    def _after_since(note):
        if not since_dt:
            return True
        date_str = note.get("Activity_Date") or note.get("date", "")
        try:
            return datetime.strptime(date_str, "%Y-%m-%d") > since_dt
        except (ValueError, TypeError):
            return True

    # 구 스키마: customer_id 필드로 필터링 가능한 경우
    old_notes = get_sales_notes(customer_id)
    old_feedbacks = []
    for note in old_notes:
        if not _after_since(note):
            continue
        feedback = note.get("Customer_Feedback") or note.get("customer_feedback")
        if feedback:
            old_feedbacks.append({
                "Activity_Date": note.get("Activity_Date") or note.get("date", ""),
                "Client_Name": note.get("Client_Name") or customer_id,
                "Sector": note.get("Sector", ""),
                "Activity_Type": note.get("Activity_Type") or note.get("channel", ""),
                "Customer_Feedback": feedback,
            })

    # 새 스키마: customer_id 없이 전체 노트 로드 후 Customer_Feedback 추출
    all_notes = _load("sales_notes.json")
    new_feedbacks = []
    for note in all_notes:
        if "customer_id" in note:
            continue  # 구 스키마 항목은 이미 위에서 처리
        if not _after_since(note):
            continue
        feedback = note.get("Customer_Feedback")
        if feedback:
            new_feedbacks.append({
                "Activity_Date": note.get("Activity_Date", ""),
                "Client_Name": note.get("Client_Name", ""),
                "Sector": note.get("Sector", ""),
                "Activity_Type": note.get("Activity_Type", ""),
                "Customer_Feedback": feedback,
            })

    feedbacks = old_feedbacks + new_feedbacks
    return {
        "customer_id": customer_id,
        "since_date": since_date,
        "feedback_count": len(feedbacks),
        "feedbacks": feedbacks,
    }


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
