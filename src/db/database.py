"""
SQLAlchemy DB 설정 및 모델 정의
- 로컬: SQLite (crm.db)
- Railway: DATABASE_URL 환경변수로 PostgreSQL 자동 전환
"""

import os
from pathlib import Path
from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import JSON

_DEFAULT_DB = f"sqlite:///{Path(__file__).parent.parent.parent / 'crm.db'}"
DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)

# Railway는 postgres:// 형식 사용 → SQLAlchemy는 postgresql:// 필요
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Persona(Base):
    __tablename__ = "personas"
    customer_id = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)


class NBAResult(Base):
    __tablename__ = "nba_results"
    customer_id = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)


class ActivitySchedule(Base):
    __tablename__ = "activities"
    customer_id = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)


class QCReport(Base):
    __tablename__ = "qc_reports"
    customer_id = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)


class SalesNote(Base):
    __tablename__ = "sales_notes"
    note_id    = Column(String, primary_key=True)
    customer_id = Column(String, nullable=False, index=True)
    data        = Column(JSON, nullable=False)


def init_db() -> None:
    """테이블 생성 (존재하면 스킵)"""
    Base.metadata.create_all(engine)
