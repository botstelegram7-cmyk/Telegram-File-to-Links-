import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
LOG_GROUP = int(os.getenv("LOG_GROUP", 0))
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 10000))

MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "TelegramStreamBot")

AUTO_DELETE_TIME = int(os.getenv("AUTO_DELETE_TIME", 0))
START_PIC = os.getenv("START_PIC", "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800")

raw_url = os.getenv("BASE_URL") or os.getenv("WEBHOOK_URL", "")
BASE_URL = raw_url.rstrip("/")
if BASE_URL.endswith(BOT_TOKEN) and BOT_TOKEN:
    BASE_URL = BASE_URL[:-len(BOT_TOKEN)].rstrip("/")

if not all([BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, MONGO_URI, BASE_URL]):
    raise ValueError("Missing mandatory variables: BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, MONGO_URI, BASE_URL")
