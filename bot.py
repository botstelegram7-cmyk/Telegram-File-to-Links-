import asyncio
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global bot instance for downloading files (used by stream handler)
bot = Bot(token=config.BOT_TOKEN)

# -------------------------------------------------------------------
# Helper: generate signed stream link
# -------------------------------------------------------------------
def generate_stream_link(file_id: str, download: bool = False, validity_hours: int = 24) -> str:
    """Create a signed URL that expires after `validity_hours`."""
    expire_ts = int(time.time() + validity_hours * 3600)
    # HMAC-SHA256 of "file_id|expire_ts"
    message = f"{file_id}|{expire_ts}".encode()
    token = hmac.new(config.STREAM_SECRET.encode(), message, hashlib.sha256).hexdigest()

    params = {
        "file_id": file_id,
        "expires": expire_ts,
        "token": token,
    }
    if download:
        params["d"] = "true"

    return f"{config.WEBHOOK_URL}/stream?{urlencode(params)}"

# -------------------------------------------------------------------
# aiohttp handler for /stream endpoint
# -------------------------------------------------------------------
async def handle_stream(request: web.Request):
    """Stream a file directly from Telegram, with expiry & token check."""
    # Parse query
    file_id = request.query.get("file_id")
    expires = request.query.get("expires")
    token = request.query.get("token")
    download = request.query.get("d", "").lower() == "true"

    # Validate required fields
    if not all([file_id, expires, token]):
        return web.Response(status=400, text="Missing parameters")

    try:
        expires_int = int(expires)
    except ValueError:
        return web.Response(status=400, text="Invalid expiry")

    # Check expiry
    if time.time() > expires_int:
        return web.Response(status=410, text="Link expired")

    # Verify token
    expected_msg = f"{file_id}|{expires_int}".encode()
    expected_token = hmac.new(config.STREAM_SECRET.encode(), expected_msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token, expected_token):
        return web.Response(status=403, text="Invalid token")

    # Get file from Telegram
    try:
        tg_file = await bot.get_file(file_id)
        file_path = tg_file.file_path
        # Telegram download URL
        telegram_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"get_file failed: {e}")
        return web.Response(status=404, text="File not found")

    # Stream the content from Telegram to the client
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(telegram_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return web.Response(status=502, text="Failed to fetch file from Telegram")

                # Prepare response headers
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                content_length = resp.headers.get("Content-Length")

                headers = {
                    "Content-Type": content_type,
                    "Accept-Ranges": "bytes",
                }
                if content_length:
                    headers["Content-Length"] = content_length

                if download:
                    # Force download
                    disposition = f'attachment; filename="{file_id}.mp4"'
                    headers["Content-Disposition"] = disposition

                # Stream response
                response = web.StreamResponse(
                    status=200,
                    headers=headers,
                )
                await response.prepare(request)

                # Pipe data in chunks
                chunk_size = 64 * 1024
                while True:
                    chunk = await resp.content.read(chunk_size)
                    if not chunk:
                        break
                    await response.write(chunk)
                await response.write_eof()
                return response

    except Exception as e:
        logger.error(f"Streaming error: {e}")
        return web.Response(status=500, text="Internal streaming error")

# -------------------------------------------------------------------
# Telegram bot handlers
# -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 कोई भी वीडियो भेजो, मैं 24 घंटे की डायरेक्ट डाउनलोड और स्ट्रीमिंग लिंक दूंगा।\n"
        "कोई अपलोड नहीं, तुरंत लिंक मिलेगा।"
    )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.video:
        file_id = message.video.file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        file_id = message.document.file_id
    else:
        await message.reply_text("❌ कृपया एक वीडियो या वीडियो डॉक्यूमेंट भेजें।")
        return

    # Generate links immediately (no upload)
    stream_link = generate_stream_link(file_id, download=False)
    download_link = generate_stream_link(file_id, download=True)

    await message.reply_text(
        f"✅ तैयार! लिंक 24 घंटे तक चलेंगे:\n\n"
        f"▶️ स्ट्रीम: {stream_link}\n"
        f"📥 डाउनलोड: {download_link}\n\n"
        f"कॉपी करके शेयर करो।",
        disable_web_page_preview=True,
    )

# -------------------------------------------------------------------
# Main – create aiohttp app, add stream route, run webhook
# -------------------------------------------------------------------
def main():
    # Create aiohttp Application
    app = web.Application()
    app.router.add_get("/stream", handle_stream)

    # Build PTB application
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    # Run webhook with the custom app (so we also serve /stream)
    application.run_webhook(
        listen="0.0.0.0",
        port=config.PORT,
        webhook_app=app,
        url_path=config.BOT_TOKEN,  # Telegram will send updates here
        webhook_url=f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}",
    )

if __name__ == "__main__":
    main()
