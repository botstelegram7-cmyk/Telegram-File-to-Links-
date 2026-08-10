import os
import time
import hmac
import hashlib
from dotenv import load_dotenv
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters as ptb_filters, ContextTypes
from pyrogram import Client

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
LOG_GROUP = int(os.getenv("LOG_GROUP", 0))
PORT = int(os.getenv("PORT", 10000))
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_key").encode()

raw_url = os.getenv("BASE_URL") or os.getenv("WEBHOOK_URL", "")
BASE_URL = raw_url.rstrip("/")
if BASE_URL.endswith(BOT_TOKEN):
    BASE_URL = BASE_URL[:-len(BOT_TOKEN)].rstrip("/")

if not all([BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, BASE_URL]):
    raise ValueError("Missing mandatory environment variables: BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, BASE_URL")

tg_client = Client(
    "StreamBotSession",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

ptb_app = Application.builder().token(BOT_TOKEN).build()

def generate_token(msg_id: int, expires: int) -> str:
    data = f"{msg_id}:{expires}"
    return hmac.new(SECRET_KEY, data.encode(), hashlib.sha256).hexdigest()

def verify_token(msg_id: int, expires: int, token: str) -> bool:
    if time.time() > expires:
        return False
    expected_token = generate_token(msg_id, expires)
    return hmac.compare_digest(expected_token, token)

HTML_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{filename} - Web Player</title>
    <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #0b0f19;
            color: #f1f5f9;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 15px;
        }}
        .player-wrapper {{
            width: 100%;
            max-width: 950px;
            background: #1e293b;
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
        }}
        .video-container {{
            width: 100%;
            background: #000;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }}
        video {{
            width: 100%;
            max-height: 75vh;
            transition: transform 0.3s ease;
        }}
        .info-panel {{
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}
        .title {{
            font-size: 1.2rem;
            font-weight: 600;
            color: #f8fafc;
            word-break: break-all;
        }}
        .controls-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: space-between;
            align-items: center;
        }}
        .buttons-group {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .btn {{
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            border: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 0.95rem;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: #3b82f6;
            color: #fff;
        }}
        .btn-primary:hover {{ background: #2563eb; }}
        .btn-secondary {{
            background: #334155;
            color: #e2e8f0;
        }}
        .btn-secondary:hover {{ background: #475569; }}
        .btn-rotate {{
            background: #10b981;
            color: #fff;
        }}
        .btn-rotate:hover {{ background: #059669; }}
    </style>
</head>
<body>
    <div class="player-wrapper">
        <div class="video-container">
            <video id="player" controls crossorigin playsinline>
                <source src="{stream_url}" type="video/mp4" />
            </video>
        </div>
        <div class="info-panel">
            <div class="title">🎬 {filename}</div>
            <div class="controls-bar">
                <div class="buttons-group">
                    <a href="{download_url}" class="btn btn-primary">📥 Download File</a>
                    <a href="{stream_url}" target="_blank" class="btn btn-secondary">🔗 Direct Stream</a>
                </div>
                <div class="buttons-group">
                    <button id="rotateBtn" class="btn btn-rotate">🔄 Rotate: 0°</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>
        const player = new Plyr('#player', {{
            controls: [
                'play-large', 'play', 'progress', 'current-time', 'duration',
                'mute', 'volume', 'settings', 'pip', 'airplay', 'fullscreen'
            ],
            settings: ['speed'],
            speed: {{ selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] }}
        }});

        let currentRotation = 0;
        const videoElement = document.querySelector('video');
        const rotateBtn = document.getElementById('rotateBtn');

        rotateBtn.addEventListener('click', () => {{
            currentRotation = (currentRotation + 90) % 360;
            videoElement.style.transform = `rotate(${{currentRotation}}deg)`;
            rotateBtn.textContent = `🔄 Rotate: ${{currentRotation}}°`;
        }});
    </script>
</body>
</html>"""

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **Welcome to Ultra-Fast Media Streamer Bot!**\n\n"
        "🚀 Send me any **Video**, **Audio**, or **Document** (up to **2GB**).\n"
        "⚡ I will generate an instant **Web Player**, **Stream Link**, and **Download Link** with high speed!"
    )
    await update.message.reply_markdown(welcome_text)

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    forwarded = await context.bot.forward_message(
        chat_id=LOG_GROUP,
        from_chat_id=update.effective_chat.id,
        message_id=msg.message_id
    )
    msg_id = forwarded.message_id

    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    filename = getattr(media, "file_name", "Telegram_Media_File.mp4")
    real_caption = msg.caption or filename

    expires = int(time.time()) + 86400
    token = generate_token(msg_id, expires)

    player_url = f"{BASE_URL}/watch?id={msg_id}&expires={expires}&token={token}"
    download_url = f"{BASE_URL}/stream?id={msg_id}&expires={expires}&token={token}&d=true"

    reply_text = (
        f"✨ **Link Generated Successfully!**\n\n"
        f"📁 **File:** `{filename}`\n"
        f"💬 **Caption:** {real_caption}\n\n"
        f"⚡ *Powered by PTB v22.8 & High-Speed MTProto*"
    )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥️ Watch Stream", url=player_url),
            InlineKeyboardButton("📥 Download", url=download_url)
        ]
    ])

    await msg.reply_markdown(reply_text, reply_markup=buttons, disable_web_page_preview=True)

ptb_app.add_handler(CommandHandler(["start", "help"], start_command))
ptb_app.add_handler(MessageHandler(
    ptb_filters.Document.ALL | ptb_filters.VIDEO | ptb_filters.AUDIO | ptb_filters.PHOTO,
    media_handler
))

async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return web.Response(status=200)
    except Exception:
        return web.Response(status=200)

async def handle_watch(request):
    msg_id = request.query.get("id")
    expires = request.query.get("expires")
    token = request.query.get("token")

    if not all([msg_id, expires, token]):
        return web.Response(text="Missing parameters", status=400)

    try:
        msg_id_int = int(msg_id)
        expires_int = int(expires)
    except ValueError:
        return web.Response(text="Invalid parameters", status=400)

    if not verify_token(msg_id_int, expires_int, token):
        return web.Response(text="Link expired or invalid token", status=403)

    msg = await tg_client.get_messages(LOG_GROUP, msg_id_int)
    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    filename = getattr(media, "file_name", "Telegram_Media_File.mp4")

    stream_url = f"{BASE_URL}/stream?id={msg_id}&expires={expires}&token={token}"
    download_url = f"{stream_url}&d=true"

    html_content = HTML_PLAYER_TEMPLATE.format(
        stream_url=stream_url,
        download_url=download_url,
        filename=filename
    )
    return web.Response(text=html_content, content_type="text/html")

async def handle_stream(request):
    msg_id = request.query.get("id")
    expires = request.query.get("expires")
    token = request.query.get("token")
    is_download = request.query.get("d") == "true"

    if not all([msg_id, expires, token]):
        return web.Response(text="Missing parameters", status=400)

    try:
        msg_id_int = int(msg_id)
        expires_int = int(expires)
    except ValueError:
        return web.Response(text="Invalid parameters", status=400)

    if not verify_token(msg_id_int, expires_int, token):
        return web.Response(text="Link expired or invalid token", status=403)

    msg = await tg_client.get_messages(LOG_GROUP, msg_id_int)
    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    
    if not media:
        return web.Response(text="Media not found or deleted from Log Group", status=404)

    filename = getattr(media, "file_name", "Telegram_Media_File.mp4")
    file_size = getattr(media, "file_size", 0)
    mime_type = getattr(media, "mime_type", "video/mp4")

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(file_size)
    }
    if is_download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    else:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'

    response = web.StreamResponse(status=200, headers=headers)
    await response.prepare(request)

    async for chunk in tg_client.stream_media(msg):
        await response.write(chunk)

    return response

async def handle_setwebhook(request):
    webhook_url = f"{BASE_URL}/{BOT_TOKEN}"
    res = await ptb_app.bot.set_webhook(url=webhook_url)
    return web.json_response({"ok": res, "url": webhook_url})

async def init_app():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="PTB v22.8 + Pyrogram 2GB Stream Server is Live!"))
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/stream", handle_stream)
    app.router.add_get("/setwebhook", handle_setwebhook)

    async def on_startup(app):
        await ptb_app.initialize()
        await ptb_app.start()
        await tg_client.start()
        webhook_url = f"{BASE_URL}/{BOT_TOKEN}"
        await ptb_app.bot.set_webhook(url=webhook_url)

    async def on_shutdown(app):
        await ptb_app.stop()
        await ptb_app.shutdown()
        await tg_client.stop()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
