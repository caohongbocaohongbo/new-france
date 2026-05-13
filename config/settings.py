"""
Pydantic Settings — 集中管理所有配置，支持 .env 文件加载
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ---- 系统 ----
    debug: bool = False
    project_dir: str = str(PROJECT_DIR)
    data_dir: str = str(PROJECT_DIR / "data")
    reports_dir: str = str(PROJECT_DIR / "reports")

    # ---- 数据库 ----
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_DIR}/data/new_france.db"
    database_url_sync: str = f"sqlite:///{PROJECT_DIR}/data/new_france.db"

    # ---- 邮件通知 ----
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = Field(default="", repr=False)
    email_to: str = ""

    # ---- API ----
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, alias="PORT")

    # ---- 策略 ----
    tracking_days: int = 30

    class Config:
        env_file = str(PROJECT_DIR / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
