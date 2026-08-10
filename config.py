import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
LOG_GROUP = int(os.getenv("LOG_GROUP", 0))
PORT = int(os.getenv("PORT", 10000))
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_key").encode()

raw_url = os.getenv("BASE_URL") or os.getenv("WEBHOOK_URL", "")
BASE_URL = raw_url.rstrip("/")
if BASE_URL.endswith(BOT_TOKEN) and BOT_TOKEN:
    BASE_URL = BASE_URL[:-len(BOT_TOKEN)].rstrip("/")

if not all([BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, BASE_URL]):
    raise ValueError("Missing mandatory environment variables: BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, BASE_URL")
  
