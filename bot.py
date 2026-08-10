import time
import json
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters as ptb_filters, ContextTypes
from pyrogram import Client

from config import (
    BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, ADMIN_ID,
    PORT, MONGO_URI, DB_NAME, AUTO_DELETE_TIME, START_PIC, BASE_URL
)

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
files_col = db["stream_files"]
users_col = db["users"]

tg_client = Client(
    "StreamBotSession",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

ptb_app = Application.builder().token(BOT_TOKEN).build()

HTML_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{filename} - MX Web Player</title>
    <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #090d16;
            color: #f8fafc;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 15px;
            overflow-x: hidden;
        }}
        .player-wrapper {{
            width: 100%;
            max-width: 1000px;
            background: #111827;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
            position: relative;
        }}
        .video-container {{
            width: 100%;
            background: #000;
            position: relative;
            touch-action: none;
        }}
        video {{
            width: 100%;
            max-height: 75vh;
            filter: brightness(1);
            transition: transform 0.3s ease;
        }}
        .gesture-toast {{
            position: absolute;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.75);
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
            display: none;
            z-index: 50;
            pointer-events: none;
        }}
        .info-panel {{
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}
        .title {{
            font-size: 1.3rem;
            font-weight: 700;
            color: #e2e8f0;
            word-break: break-all;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .btn {{
            padding: 10px 20px;
            border-radius: 10px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
            border: none;
            cursor: pointer;
        }}
        .btn-download {{ background: #2563eb; color: #fff; }}
        .btn-download:hover {{ background: #1d4ed8; }}
        .btn-stream {{ background: #334155; color: #fff; }}
        .btn-stream:hover {{ background: #475569; }}
        .playlist-container {{
            width: 100%;
            max-width: 1000px;
            margin-top: 25px;
            background: #111827;
            border-radius: 16px;
            padding: 20px;
        }}
        .playlist-header {{
            font-size: 1.2rem;
            font-weight: 700;
            margin-bottom: 15px;
            color: #38bdf8;
        }}
        .playlist-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            background: #1f2937;
            border-radius: 10px;
            margin-bottom: 10px;
            text-decoration: none;
            color: #f1f5f9;
            transition: background 0.2s;
        }}
        .playlist-item:hover {{ background: #374151; }}
        .playlist-item.active {{ border-left: 4px solid #38bdf8; background: #1e293b; }}
    </style>
</head>
<body>
    <div class="player-wrapper">
        <div class="video-container" id="touchArea">
            <div id="gestureToast" class="gesture-toast"></div>
            <video id="player" controls crossorigin playsinline>
                <source src="{stream_url}" type="video/mp4" />
            </video>
        </div>
        <div class="info-panel">
            <div class="title">🎬 {filename}</div>
            <div class="actions">
                <a href="{download_url}" class="btn btn-download">📥 Instant Download</a>
                <a href="{stream_url}" target="_blank" class="btn btn-stream">🔗 Direct URL</a>
            </div>
        </div>
    </div>

    <div class="playlist-container">
        <div class="playlist-header">📺 Your Recent Videos (Newest Top)</div>
        <div id="playlist">
            {playlist_html}
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

        const touchArea = document.getElementById('touchArea');
        const videoElement = document.querySelector('video');
        const toast = document.getElementById('gestureToast');

        let startY = 0;
        let startX = 0;
        let currentBrightness = 1;

        function showToast(text) {{
            toast.textContent = text;
            toast.style.display = 'block';
            clearTimeout(window.toastTimer);
            window.toastTimer = setTimeout(() => {{ toast.style.display = 'none'; }}, 1000);
        }}

        touchArea.addEventListener('touchstart', (e) => {{
            if (e.touches.length === 1) {{
                startY = e.touches[0].clientY;
                startX = e.touches[0].clientX;
            }}
        }});

        touchArea.addEventListener('touchmove', (e) => {{
            if (e.touches.length === 1) {{
                const deltaY = (startY - e.touches[0].clientY) / 150;
                const screenWidth = window.innerWidth;

                if (startX < screenWidth / 2) {{
                    currentBrightness = Math.min(2, Math.max(0.2, currentBrightness + deltaY * 0.05));
                    videoElement.style.filter = `brightness(${{currentBrightness}})`;
                    showToast(`☀️ Brightness: ${{Math.round(currentBrightness * 100)}}%`);
                }} else {{
                    player.volume = Math.min(1, Math.max(0, player.volume + deltaY * 0.05));
                    showToast(`🔊 Volume: ${{Math.round(player.volume * 100)}}%`);
                }}
                startY = e.touches[0].clientY;
            }}
        }});
    </script>
</body>
</html>"""

MX_REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Opening in MX Player...</title>
    <style>
        body {{
            background: #090d16;
            color: #f8fafc;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
            text-align: center;
        }}
        .card {{
            background: #111827;
            padding: 30px;
            border-radius: 16px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
            max-width: 450px;
            width: 100%;
        }}
        .btn {{
            display: inline-block;
            margin-top: 20px;
            padding: 14px 28px;
            border-radius: 12px;
            background: #2563eb;
            color: #fff;
            text-decoration: none;
            font-weight: bold;
            width: 100%;
        }}
        .btn-sec {{ background: #334155; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>🎬 Launching MX Player...</h2>
        <p style="margin-top: 10px; color: #94a3b8;">If MX Player is installed, your video will play automatically.</p>
        <a href="intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;type=video/*;title={filename};end" class="btn">🟢 Open MX Player (Free)</a>
        <a href="intent:{stream_url}#Intent;package=com.mxtech.videoplayer.pro;type=video/*;title={filename};end" class="btn" style="background:#059669;">🟢 Open MX Player (Pro)</a>
        <a href="{web_url}" class="btn btn-sec">🔵 Watch in Web Player</a>
    </div>
    <script>
        setTimeout(() => {{
            window.location.href = "intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;type=video/*;title={filename};end";
        }}, 800);
    </script>
</body>
</html>"""

async def send_raw_telegram_message(chat_id, text, reply_markup=None, photo_url=None):
    async with aiohttp.ClientSession() as session:
        payload = {
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        if photo_url:
            payload["photo"] = photo_url
            payload["caption"] = text
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        else:
            payload["text"] = text
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        async with session.post(url, json=payload) as resp:
            return await resp.json()

async def save_user(user):
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {"name": user.full_name, "username": user.username}},
        upsert=True
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user(update.effective_user)
    welcome_text = (
        f"👋 <b>Hello {update.effective_user.first_name}!</b>\n\n"
        "🎬 <b>MX Ultra Streamer Bot</b> is Ready!\n"
        "⚡ Send me any <b>Video, Audio, or Document</b> (up to 2GB).\n\n"
        "<blockquote>🌟 <b>Features:</b>\n"
        "• High-Speed Stream & Download Links\n"
        "• YouTube-Style Recent Video Playlist\n"
        "• Mobile Swipe Gestures (Brightness & Volume)\n"
        "• Permanent Cloud Storage via MongoDB</blockquote>"
    )

    buttons = {
        "inline_keyboard": [
            [
                {"text": "🟢 ✨ Web App Streamer", "web_app": {"url": f"{BASE_URL}"}},
                {"text": "🔵 📖 Help Guide", "callback_data": "help"}
            ]
        ]
    }

    await send_raw_telegram_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=buttons,
        photo_url=START_PIC if START_PIC else None
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 <b>How To Use MX Streamer Bot:</b>\n\n"
        "<blockquote>1️⃣ Simply forward or send any media file here.\n"
        "2️⃣ I will upload it safely and generate Permanent Links.\n"
        "3️⃣ In the Video Player:\n"
        "    • <b>Swipe Left (Up/Down):</b> Adjust Brightness ☀️\n"
        "    • <b>Swipe Right (Up/Down):</b> Adjust Volume 🔊\n"
        "    • <b>Below Player:</b> Your recent videos appear like a playlist!</blockquote>"
    )
    buttons = {
        "inline_keyboard": [
            [{"text": "⚡ Close Help", "callback_data": "close"}]
        ]
    }
    await send_raw_telegram_message(update.effective_chat.id, help_text, buttons)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    result = await files_col.delete_many({})
    await update.message.reply_text(f"🗑️ **Database Cleared!**\n\n✅ Permanently deleted `{result.deleted_count}` saved files from MongoDB.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to any message with `/broadcast` to send it to all users.")
        return

    sent = 0
    async for user_doc in users_col.find():
        try:
            await context.bot.copy_message(
                chat_id=user_doc["_id"],
                from_chat_id=update.effective_chat.id,
                message_id=update.message.reply_to_message.message_id
            )
            sent += 1
        except Exception:
            continue

    await update.message.reply_text(f"✅ Broadcast completed! Sent to {sent} users.")

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)
    msg = update.effective_message

    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    filename = getattr(media, "file_name", f"Video_{int(time.time())}.mp4")
    original_caption = msg.caption or ""

    profile_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    log_caption = (
        f"📁 **File:** `{filename}`\n"
        f"📝 **Caption:** {original_caption}\n\n"
        f"👤 **Uploaded By:** [{user.full_name}]({profile_url}) `({user.id})`"
    )

    log_msg = await context.bot.copy_message(
        chat_id=LOG_GROUP,
        from_chat_id=update.effective_chat.id,
        message_id=msg.message_id,
        caption=log_caption,
        parse_mode=ParseMode.MARKDOWN
    )

    file_doc = {
        "msg_id": log_msg.message_id,
        "user_id": user.id,
        "filename": filename,
        "caption": original_caption,
        "created_at": int(time.time())
    }
    await files_col.insert_one(file_doc)

    mx_url = f"{BASE_URL}/mx?id={log_msg.message_id}"
    watch_url = f"{BASE_URL}/watch?id={log_msg.message_id}"
    download_url = f"{BASE_URL}/stream?id={log_msg.message_id}&d=true"
    direct_url = f"{BASE_URL}/stream?id={log_msg.message_id}"

    reply_text = (
        f"✨ **Permanent Stream & Download Ready!**\n\n"
        f"🎬 **File:** `{filename}`\n\n"
        f"💡 *Tip: Play in MX Player app or use our swipe-gesture Web Player!*"
    )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Play in MX Player (App)", url=mx_url)
        ],
        [
            InlineKeyboardButton("🔵 Watch in Web Player", url=watch_url)
        ],
        [
            InlineKeyboardButton("📥 Instant Download", url=download_url),
            InlineKeyboardButton("🔗 Direct URL", url=direct_url)
        ]
    ])

    await msg.reply_markdown(reply_text, reply_markup=buttons, disable_web_page_preview=True)

ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(CommandHandler("help", help_command))
ptb_app.add_handler(CommandHandler("clear", clear_command))
ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
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
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File not found in Database or Expired", status=404)

    if AUTO_DELETE_TIME > 0:
        if int(time.time()) > file_doc["created_at"] + AUTO_DELETE_TIME:
            await files_col.delete_one({"msg_id": msg_id})
            return web.Response(text="Link Expired (Auto Delete Timer Triggered)", status=403)

    user_id = file_doc["user_id"]
    playlist_cursor = files_col.find({"user_id": user_id}).sort("created_at", -1).limit(10)
    playlist_html = ""

    async for item in playlist_cursor:
        active_class = "active" if item["msg_id"] == msg_id else ""
        item_url = f"{BASE_URL}/watch?id={item['msg_id']}"
        playlist_html += (
            f'<a href="{item_url}" class="playlist-item {active_class}">'
            f'<span>🎬 {item["filename"]}</span>'
            f'<span>▶️ Play</span>'
            f'</a>'
        )

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"

    html_content = HTML_PLAYER_TEMPLATE.format(
        stream_url=stream_url,
        download_url=download_url,
        filename=file_doc["filename"],
        playlist_html=playlist_html
    )
    return web.Response(text=html_content, content_type="text/html")

async def handle_mx_redirect(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File not found", status=404)

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    web_url = f"{BASE_URL}/watch?id={msg_id}"

    html_content = MX_REDIRECT_TEMPLATE.format(
        stream_url=stream_url,
        web_url=web_url,
        filename=file_doc["filename"]
    )
    return web.Response(text=html_content, content_type="text/html")

async def handle_stream(request):
    msg_id_str = request.query.get("id")
    is_download = request.query.get("d") == "true"

    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File not found", status=404)

    if AUTO_DELETE_TIME > 0:
        if int(time.time()) > file_doc["created_at"] + AUTO_DELETE_TIME:
            return web.Response(text="Link Expired", status=403)

    msg = await tg_client.get_messages(LOG_GROUP, msg_id)
    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    if not media:
        return web.Response(text="Media missing in storage channel", status=404)

    filename = getattr(media, "file_name", file_doc["filename"])
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
    app.router.add_get("/", lambda r: web.Response(text="Enterprise MX Media Streamer is Live!"))
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/mx", handle_mx_redirect)
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
        mongo_client.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=PORT)
