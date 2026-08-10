import asyncio
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

from aiohttp import web
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Global bot instance (will be initialized at startup)
bot = Bot(token=config.BOT_TOKEN)

# Global application (will be built and initialized at startup)
application = None


# -------------------------------------------------------------------
# Helper: generate signed stream link
# -------------------------------------------------------------------
def generate_stream_link(file_id: str, download: bool = False, validity_hours: int = 24) -> str:
    expire_ts = int(time.time() + validity_hours * 3600)
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
    file_id = request.query.get("file_id")
    expires = request.query.get("expires")
    token = request.query.get("token")
    download = request.query.get("d", "").lower() == "true"

    if not all([file_id, expires, token]):
        return web.Response(status=400, text="Missing parameters")

    try:
        expires_int = int(expires)
    except ValueError:
        return web.Response(status=400, text="Invalid expiry")

    if time.time() > expires_int:
        return web.Response(status=410, text="Link expired")

    expected_msg = f"{file_id}|{expires_int}".encode()
    expected_token = hmac.new(config.STREAM_SECRET.encode(), expected_msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token, expected_token):
        return web.Response(status=403, text="Invalid token")

    try:
        # bot is already initialized, so get_file works fine
        tg_file = await bot.get_file(file_id)
        file_path = tg_file.file_path
        telegram_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"get_file failed: {e}")
        return web.Response(status=404, text="File not found")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(telegram_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return web.Response(status=502, text="Failed to fetch file from Telegram")

                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                content_length = resp.headers.get("Content-Length")

                headers = {
                    "Content-Type": content_type,
                    "Accept-Ranges": "bytes",
                }
                if content_length:
                    headers["Content-Length"] = content_length

                if download:
                    headers["Content-Disposition"] = f'attachment; filename="{file_id}.mp4"'
                else:
                    headers["Content-Disposition"] = "inline"

                response = web.StreamResponse(status=200, headers=headers)
                await response.prepare(request)

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
        "कोई अपलोड नहीं, तुरंत बटन के साथ लिंक मिलेगा।"
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

    stream_link = generate_stream_link(file_id, download=False)
    download_link = generate_stream_link(file_id, download=True)

    keyboard = [
        [InlineKeyboardButton("▶️ Stream", url=stream_link)],
        [InlineKeyboardButton("📥 Download", url=download_link)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        "✅ ये रहे तुम्हारे लिंक (24 घंटे के लिए वैध):",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


# -------------------------------------------------------------------
# Main: setup aiohttp app, PTB webhook processing, and start server
# -------------------------------------------------------------------
def main():
    global application

    # Build PTB application
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    # Create aiohttp web app
    app = web.Application()

    # POST route for Telegram webhook
    async def webhook_handler(request: web.Request):
        if request.headers.get("content-type") == "application/json":
            data = await request.json()
            # Process the update with PTB (both bot and application are already initialized)
            await application.process_update(Update.de_json(data, bot))
            return web.Response()
        return web.Response(status=400)

    app.router.add_post(f"/{config.BOT_TOKEN}", webhook_handler)

    # Our custom stream endpoint
    app.router.add_get("/stream", handle_stream)

    # Set webhook on startup, initialize both bot and application
    async def on_startup(app):
        # Initialize Bot first
        await bot.initialize()
        # Then initialize Application
        await application.initialize()
        # Set webhook
        webhook_url = f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}"
        await bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")

    app.on_startup.append(on_startup)

    # Run the aiohttp server
    web.run_app(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()