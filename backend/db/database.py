"""
数据库连接 — SQLite + SQLAlchemy (同步 + 异步)
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_DIR / "data"
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/new_france.db"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    """初始化数据库表"""
    from .models import register_models  # noqa
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖：获取DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
