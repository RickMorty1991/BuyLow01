import sqlite3

DB_PATH = "etf.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS etfs (
                ticker TEXT PRIMARY KEY,
                target_price REAL
            )
        """)
        conn.commit()


# ========= API, ЯКИЙ ВИКОРИСТОВУЄ БОТ =========

def add_etf(ticker: str, target_price: float | None = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO etfs (ticker, target_price) VALUES (?, ?)",
            (ticker.upper(), target_price)
        )
        conn.commit()


def remove_etf(ticker: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM etfs WHERE ticker = ?",
            (ticker.upper(),)
        )
        conn.commit()


def get_all_etfs():
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ticker, target_price FROM etfs ORDER BY ticker"
        )
        return cur.fetchall()
