import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g., https://your-app.onrender.com

# Random secret key for signing stream links (generate once and keep it safe)
STREAM_SECRET = os.getenv("STREAM_SECRET", os.urandom(32).hex())
