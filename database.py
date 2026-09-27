import sqlite3
from config import DATABASE_PATH


def get_connection():
    return sqlite3.connect(DATABASE_PATH)


def init_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            group_name TEXT,
            enabled INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER,
            user_id INTEGER,
            warning_count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)

    conn.commit()
    conn.close()