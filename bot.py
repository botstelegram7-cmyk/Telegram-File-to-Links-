import asyncio
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web, ClientTimeout
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

bot = Bot(token=config.BOT_TOKEN)
application = None


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

    # Get Telegram file URL
    try:
        tg_file = await bot.get_file(file_id)
        file_path = tg_file.file_path
        telegram_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
        logger.info(f"Fetching file from Telegram: {telegram_url[:80]}...")
    except Exception as e:
        logger.error(f"get_file failed: {e}")
        return web.Response(status=404, text="File not found on Telegram")

    # Stream / download the file
    try:
        timeout = ClientTimeout(total=120)  # 2 minutes for large files
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(telegram_url) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram file server returned {resp.status}")
                    return web.Response(
                        status=502,
                        text=f"Failed to fetch file (Telegram status {resp.status})",
                    )

                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                content_length = resp.headers.get("Content-Length")
                headers = {"Content-Type": content_type, "Accept-Ranges": "bytes"}
                if content_length:
                    headers["Content-Length"] = content_length
                if download:
                    headers["Content-Disposition"] = f'attachment; filename="{file_id}.mp4"'
                else:
                    headers["Content-Disposition"] = "inline"

                stream_resp = web.StreamResponse(status=200, headers=headers)
                await stream_resp.prepare(request)

                chunk_size = 64 * 1024
                while True:
                    chunk = await resp.content.read(chunk_size)
                    if not chunk:
                        break
                    await stream_resp.write(chunk)
                await stream_resp.write_eof()
                return stream_resp

    except asyncio.TimeoutError:
        logger.error("Timeout while fetching file from Telegram")
        return web.Response(status=504, text="Upstream timeout")
    except aiohttp.ClientError as e:
        logger.error(f"Client error fetching file: {e}")
        return web.Response(status=502, text="Connection error")
    except Exception as e:
        logger.error(f"Unexpected streaming error: {e}")
        return web.Response(status=500, text="Internal streaming error")


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


def main():
    global application
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    app = web.Application()

    async def webhook_handler(request: web.Request):
        if request.headers.get("content-type") == "application/json":
            data = await request.json()
            await application.process_update(Update.de_json(data, bot))
            return web.Response()
        return web.Response(status=400)

    app.router.add_post(f"/{config.BOT_TOKEN}", webhook_handler)
    app.router.add_get("/stream", handle_stream)

    async def on_startup(app):
        await bot.initialize()
        await application.initialize()
        webhook_url = f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}"
        await bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")

    app.on_startup.append(on_startup)
    web.run_app(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()