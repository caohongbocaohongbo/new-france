"""
数据库连接 — SQLite + SQLAlchemy (同步 + 异步)

P3 稳定性（性能优化方案 §7）：
- 连接级 timeout=5s + PRAGMA busy_timeout=5000（连接事件统一注入）
- journal_mode=WAL 在 init_db 受控初始化并校验；synchronous=NORMAL（WAL 下）
- foreign_keys=ON（显式声明，不依赖默认）
- 不启用 pool_pre_ping（本地文件库无需 TCP 断连探测）
"""
import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_DIR / "data"
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/new_france.db"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 5},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
    """每个新连接的 PRAGMA（P3 §7.1）：busy_timeout / foreign_keys / synchronous。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _journal_mode(conn) -> str:
    return str(conn.execute(text("PRAGMA journal_mode")).scalar() or "").lower()


def init_db():
    """初始化数据库表 + 受控开启 WAL 并校验结果（不依赖每个新连接重复切换）。"""
    from .models import register_models  # noqa
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        before = _journal_mode(conn)
        if before != "wal":
            conn.execute(text("PRAGMA journal_mode=WAL"))
        after = _journal_mode(conn)
        if after != "wal":
            logger.warning("SQLite WAL 开启失败: %s -> %s（继续运行，锁等待依赖 busy_timeout）", before, after)
        else:
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            logger.info("SQLite journal_mode=wal 已生效")


def get_db():
    """FastAPI 依赖：获取DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
