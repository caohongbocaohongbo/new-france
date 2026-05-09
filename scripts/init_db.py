#!/usr/bin/env python3
"""初始化数据库"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.db.database import init_db
init_db()
print("Database initialized successfully")
