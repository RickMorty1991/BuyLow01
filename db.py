import sqlite3

from threading import Lock

from config import DB_PATH



db_lock = Lock()

db = sqlite3.connect(DB_PATH, check_same_thread=False)

c = db.cursor()



with db_lock:

    c.execute("""

    CREATE TABLE IF NOT EXISTS subs(

        chat_id INTEGER,

        ticker TEXT,

        threshold REAL DEFAULT 5,

        rebound_enabled INTEGER DEFAULT 1,

        last_alerted INTEGER DEFAULT 0,

        rebound_sent INTEGER DEFAULT 0,

        price_365d_ago REAL DEFAULT 0,

        PRIMARY KEY(chat_id, ticker)

    )

    """)

    db.commit()



def get_subs(chat_id=None):

    with db_lock:

        if chat_id:

            return c.execute("SELECT ticker, threshold, rebound_enabled FROM subs WHERE chat_id=?", (chat_id,)).fetchall()

        return c.execute("SELECT chat_id, ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_365d_ago FROM subs").fetchall()



def add_sub(chat_id, ticker, threshold=5, rebound_enabled=1, last_alerted=0, rebound_sent=0, price_365d_ago=0):

    with db_lock:

        c.execute("""

        INSERT OR IGNORE INTO subs(chat_id, ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_365d_ago)

        VALUES(?,?,?,?,?,?,?)

        """, (chat_id, ticker, threshold, rebound_enabled, last_alerted, rebound_sent, price_365d_ago))

        db.commit()



def remove_sub(chat_id, ticker):

    with db_lock:

        c.execute("DELETE FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker))

        db.commit()



def update_threshold(chat_id, ticker, threshold):

    with db_lock:

        c.execute("UPDATE subs SET threshold=?, last_alerted=0, rebound_sent=0 WHERE chat_id=? AND ticker=?", (threshold, chat_id, ticker))

        db.commit()



def toggle_rebound(chat_id, ticker):

    with db_lock:

        row = c.execute("SELECT rebound_enabled FROM subs WHERE chat_id=? AND ticker=?", (chat_id, ticker)).fetchone()

        new = 0 if row and row[0] == 1 else 1

        c.execute("UPDATE subs SET rebound_enabled=?, rebound_sent=0 WHERE chat_id=? AND ticker=?", (new, chat_id, ticker))

        db.commit()


        return new

