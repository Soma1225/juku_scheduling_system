# -*- coding: utf-8 -*-
"""
db.py

DB接続とスキーマ初期化だけを担当するモジュール。
他のどのファイルよりも「下」のレイヤーにあり、これ自身は他の自作モジュールに依存しない。
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "juku_schedule.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

DEFAULT_PERIODS = [
    (1, "14:20", "15:40"), (2, "15:50", "17:10"), (3, "17:20", "18:40"),
    (4, "19:00", "20:20"), (5, "20:30", "21:50"),
]


def ensure_db_exists() -> None:
    """DBファイルが無ければ schema.sql から作成し、PERIODSが空なら初期値を投入する。"""
    if not DB_PATH.exists():
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"schema.sql が見つかりません: {SCHEMA_PATH}")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT COUNT(*) FROM PERIODS").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO PERIODS (period_number, start_time, end_time) VALUES (?, ?, ?)",
            DEFAULT_PERIODS,
        )
        conn.commit()
    conn.close()


def get_conn() -> sqlite3.Connection:
    """外部キー制約を有効にした状態でDB接続を返す。呼び出し側でclose()すること。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
