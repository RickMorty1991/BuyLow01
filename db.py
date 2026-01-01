import sqlite3
from config import DB_PATH


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS subs (
            chat_id INTEGER,
            ticker TEXT,
            threshold REAL,
            rebound_enabled INTEGER DEFAULT 0,
            rebound_sent INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, ticker)
        )
        """)
