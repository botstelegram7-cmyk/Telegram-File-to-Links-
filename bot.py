import os
import time
import hmac
import hashlib
import logging
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

# HTML Web Video Player Template
HTML_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Video - Web Player</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background-color: #0f172a;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .player-container {{
            width: 100%;
            max-width: 900px;
            background: #1e293b;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        video {{
            width: 100%;
            max-height: 70vh;
            display: block;
            outline: none;
            background: #000;
        }}
        .info-panel {{
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}
        .file-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #e2e8f0;
            word-break: break-all;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            text-decoration: none;
            transition: background 0.2s;
            cursor: pointer;
        }}
        .btn-primary {{
            background-color: #2563eb;
            color: #ffffff;
        }}
        .btn-primary:hover {{ background-color: #1d4ed8; }}
        .btn-secondary {{
            background-color: #334155;
            color: #cbd5e1;
        }}
        .btn-secondary:hover {{ background-color: #475569; }}
    </style>
</head>
<body>
    <div class="player-container">
        <video controls autoplay name="media">
            <source src="{stream_raw_url}" type="video/mp4">
            Your browser does not support the video tag.
        </video>
        <div class="info-panel">
            <div class="file-title">📁 {filename}</div>
            <div class="actions">
                <a href="{download_raw_url}" class="btn btn-primary">📥 Download File</a>
                <a href="{stream_raw_url}" target="_blank" class="btn btn-secondary">🔗 Direct Stream URL</a>
            </div>
        </div>
    </div>
</body>
</html>"""

# Webhook Handler
async def handle_webhook(request):
    try:
        data = await request.json()
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            
            file_id = None
            if "document" in message:
                file_id = message["document"]["file_id"]
            elif "video" in message:
                file_id = message["video"]["file_id"]
            elif "audio" in message:
                file_id = message["audio"]["file_id"]
            elif "photo" in message:
                file_id = message["photo"][-1]["file_id"]

            if file_id:
                expires = int(time.time()) + 86400  # 24 Hours validity
                token = generate_token(file_id, expires)
                
                player_url = f"{BASE_URL}/watch?file_id={file_id}&expires={expires}&token={token}"
                download_url = f"{BASE_URL}/stream?file_id={file_id}&expires={expires}&token={token}&d=true"
                
                reply_text = (
                    f"✨ **Link Generated Successfully!**\n\n"
                    f"🎬 **Web Video Player:** {player_url}\n\n"
                    f"📥 **Direct Download Link:** {download_url}\n\n"
                    f"⚠️ *Note: Standard Bot API supports files up to 20MB. For larger files, Client API (Pyrogram) mode is required.*"
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
                        "text": "Please send a video, audio, or document to generate streaming links."
                    })
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return web.Response(status=500)

# Web Video Player Page Handler
async def handle_watch(request):
    file_id = request.query.get("file_id")
    expires = request.query.get("expires")
    token = request.query.get("token")

    if not file_id or not expires or not token:
        return web.Response(text="Missing parameters", status=400)

    try:
        expires_int = int(expires)
    except ValueError:
        return web.Response(text="Invalid expiration parameter", status=400)

    if not verify_token(file_id, expires_int, token):
        return web.Response(text="Link expired or invalid token", status=403)

    stream_raw_url = f"{BASE_URL}/stream?file_id={file_id}&expires={expires}&token={token}"
    download_raw_url = f"{stream_raw_url}&d=true"
    
    html_content = HTML_PLAYER_TEMPLATE.format(
        stream_raw_url=stream_raw_url,
        download_raw_url=download_raw_url,
        filename="Telegram Media File"
    )
    return web.Response(text=html_content, content_type="text/html")

# Raw Stream / Download Handler
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
                text="Unable to fetch file. File exceeds Telegram Bot API 20MB limit or was deleted.", 
                status=404
            )
        
        file_path = res_data["result"]["file_path"]

    telegram_file_url = f"{TELEGRAM_FILE_API}/{file_path}"
    filename = os.path.basename(file_path)

    session = ClientSession()
    tg_resp = await session.get(telegram_file_url)

    if tg_resp.status != 200:
        await session.close()
        return web.Response(text="Telegram file server error or file > 20MB limit.", status=tg_resp.status)

    headers = {}
    if is_download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    response = web.StreamResponse(status=200, headers=headers)
    response.content_type = tg_resp.content_type or "video/mp4"
    
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
    
    # Root route automatically handles GET and HEAD requests without crash
    app.router.add_get("/", lambda r: web.Response(text="Bot Stream Server & Web Player is Running!"))
    
    # Main routes
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    app.router.add_get("/watch", handle_watch)
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
