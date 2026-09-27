import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.sqlite3")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing. Check your .env file.")