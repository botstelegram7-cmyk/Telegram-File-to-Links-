import os
import time
import hmac
import hashlib
import logging
import urllib.parse
from aiohttp import web, ClientSession
import httpx

# Logging Config
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 10000))
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret_key").encode()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing!")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

# Helpers for Signed URLs
def generate_token(file_id: str, expires: int) -> str:
    data = f"{file_id}:{expires}"
    return hmac.new(SECRET_KEY, data.encode(), hashlib.sha256).hexdigest()

def verify_token(file_id: str, expires: int, token: str) -> bool:
    if time.time() > expires:
        return False
    expected_token = generate_token(file_id, expires)
    return hmac.compare_digest(expected_token, token)

# Webhook Handler
async def handle_webhook(request):
    try:
        data = await request.json()
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            
            # Check for file attachments
            file_id = None
            file_name = "file"
            
            if "document" in message:
                file_id = message["document"]["file_id"]
                file_name = message["document"].get("file_name", "document")
            elif "video" in message:
                file_id = message["video"]["file_id"]
                file_name = message["video"].get("file_name", "video.mp4")
            elif "audio" in message:
                file_id = message["audio"]["file_id"]
                file_name = message["audio"].get("file_name", "audio.mp3")
            elif "photo" in message:
                file_id = message["photo"][-1]["file_id"]
                file_name = "photo.jpg"

            if file_id:
                expires = int(time.time()) + 86400  # 24 Hours validity
                token = generate_token(file_id, expires)
                
                stream_url = f"{BASE_URL}/stream?file_id={file_id}&expires={expires}&token={token}"
                download_url = f"{stream_url}&d=true"
                
                reply_text = (
                    f"✨ **Link Generated Successfully!**\n\n"
                    f"🔗 **Stream Link:** {stream_url}\n\n"
                    f"📥 **Download Link:** {download_url}\n\n"
                    f"⚠️ *Note: Bot API supports direct downloading for files up to 20MB only.*"
                )
                
                async with httpx.AsyncClient() as client:
                    await client.post(f"{TELEGRAM_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": reply_text,
                        "parse_mode": "Markdown"
                    })
            else:
                async with httpx.AsyncClient() as client:
                    await client.post(f"{TELEGRAM_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "Please send a file, video, audio, or document to generate links."
                    })
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return web.Response(status=500)

# Stream / Download Handler
async def handle_stream(request):
    file_id = request.query.get("file_id")
    expires = request.query.get("expires")
    token = request.query.get("token")
    is_download = request.query.get("d") == "true"

    if not file_id or not expires or not token:
        return web.Response(text="Missing parameters", status=400)

    try:
        expires_int = int(expires)
    except ValueError:
        return web.Response(text="Invalid expiration parameter", status=400)

    if not verify_token(file_id, expires_int, token):
        return web.Response(text="Link expired or invalid token", status=403)

    # Get File Path from Telegram
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{TELEGRAM_API}/getFile", json={"file_id": file_id})
        res_data = res.json()
        
        if not res_data.get("ok"):
            return web.Response(
                text="Unable to fetch file path from Telegram. File might be larger than 20MB limit or deleted.", 
                status=404
            )
        
        file_path = res_data["result"]["file_path"]

    telegram_file_url = f"{TELEGRAM_FILE_API}/{file_path}"
    filename = os.path.basename(file_path)

    # Stream the file from Telegram server to client
    session = ClientSession()
    tg_resp = await session.get(telegram_file_url)

    if tg_resp.status != 200:
        await session.close()
        logger.error(f"Telegram file server returned {tg_resp.status}")
        return web.Response(text="Telegram file server error or file > 20MB.", status=tg_resp.status)

    headers = {}
    if is_download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    response = web.StreamResponse(
        status=200,
        headers=headers
    )
    response.content_type = tg_resp.content_type or "application/octet-stream"
    
    await response.prepare(request)

    try:
        async for chunk in tg_resp.content.iter_chunked(64 * 1024):
            await response.write(chunk)
    finally:
        await session.close()

    return response

# Web Server Initialization
async def init_app():
    app = web.Application()
    
    # Health check route
    app.router.add_get("/", lambda r: web.Response(text="Bot Stream Server is Running!"))
    app.router.add_head("/", lambda r: web.Response(status=200))
    
    # Main routes
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    app.router.add_get("/stream", handle_stream)
    
    # Set Webhook
    webhook_url = f"{BASE_URL}/{BOT_TOKEN}"
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/setWebhook", json={"url": webhook_url})
        logger.info(f"Webhook set to {webhook_url}")
        
    return app

if __name__ == "__main__":
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
