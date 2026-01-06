import sqlite3

DB_PATH = "etf.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS etfs (
            ticker TEXT PRIMARY KEY,
            target_price REAL,
            rebound INTEGER DEFAULT 0,
            last_price REAL
        )
        """)
        conn.commit()


def add_etf(ticker):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO etfs (ticker) VALUES (?)",
            (ticker.upper(),)
        )
        conn.commit()


def get_all_etfs():
    with get_conn() as conn:
        return conn.execute(
            "SELECT ticker, target_price FROM etfs"
        ).fetchall()


def set_threshold(ticker, price):
    with get_conn() as conn:
        conn.execute(
            "UPDATE etfs SET target_price=? WHERE ticker=?",
            (price, ticker.upper())
        )
        conn.commit()


def toggle_rebound():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT rebound FROM etfs LIMIT 1"
        ).fetchone()

        new_state = 0 if row and row[0] else 1
        conn.execute("UPDATE etfs SET rebound=?", (new_state,))
        conn.commit()
        return bool(new_state)


def get_monitor_data():
    with get_conn() as conn:
        return conn.execute(
            "SELECT ticker, target_price, rebound, last_price FROM etfs"
        ).fetchall()


def update_last_price(ticker, price):
    with get_conn() as conn:
        conn.execute(
            "UPDATE etfs SET last_price=? WHERE ticker=?",
            (price, ticker)
        )
        conn.commit()
