import time
import urllib.parse
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

# --- MONGODB CONNECTION ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
files_col = db["stream_files"]
users_col = db["users"]

# --- PYROGRAM CLIENT (FOR STREAMING MEDIA) ---
tg_client = Client(
    "StreamBotSession",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# --- PYTHON TELEGRAM BOT APPLICATION ---
ptb_app = Application.builder().token(BOT_TOKEN).build()

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
        video, audio {{
            width: 100%;
            max-height: 75vh;
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
        <div class="video-container">
            <video id="player" controls crossorigin playsinline>
                <source src="{stream_url}" />
            </video>
        </div>
        <div class="info-panel">
            <div class="title">📁 {filename}</div>
            <div class="actions">
                <a href="{download_url}" class="btn btn-download">📥 Instant Download</a>
                <a href="{stream_url}" target="_blank" class="btn btn-stream">🔗 Direct URL</a>
            </div>
        </div>
    </div>

    <div class="playlist-container">
        <div class="playlist-header">📺 Your Recent Files</div>
        <div id="playlist">
            {playlist_html}
        </div>
    </div>

    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>
        const player = new Plyr('#player');
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
    """Permanently saves user details in MongoDB so IDs persist across redeploys."""
    await users_col.update_one(
        {"_id": user.id},
        {
            "$set": {
                "name": user.full_name,
                "username": user.username,
                "last_seen": int(time.time())
            }
        },
        upsert=True
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return  # Groups me start command handle nahi hogi
    
    await save_user(update.effective_user)
    welcome_text = (
        f"👋 <b>Hello {update.effective_user.first_name}!</b>\n\n"
        f"⚡ <b>Media Streamer & Downloader Bot</b> is Active!\n"
        f"<blockquote>Send me any Video, Audio, Photo, or Document to get instant links.</blockquote>"
    )

    # Using native inline button styles (primary, success, danger) supported in PTB/Bot API
    buttons = {
        "inline_keyboard": [
            [
                {"text": "✨ Open Web App", "web_app": {"url": f"{BASE_URL}"}, "style": "primary"},
                {"text": "📖 Help Guide", "callback_data": "help", "style": "success"}
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
        "📖 <b>Bot Assistance Guide:</b>\n\n"
        "<blockquote>• Send Videos/Audio to get both <b>Streaming</b> & <b>Download</b> links.\n"
        "• Send Photos, Zips, or Documents to get direct <b>Download</b> & <b>Web View</b> links.\n"
        "• In groups, tag or mention the bot along with your file/command to trigger it.</blockquote>"
    )
    buttons = {
        "inline_keyboard": [
            [{"text": "❌ Close Guide", "callback_data": "close", "style": "danger"}]
        ]
    }
    await send_raw_telegram_message(update.effective_chat.id, help_text, buttons)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    result = await files_col.delete_many({})
    await update.message.reply_text(
        f"🗑️ <b>Database Cleared!</b> Deleted <code>{result.deleted_count}</code> files.",
        parse_mode=ParseMode.HTML
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to any message to broadcast.")
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

    await update.message.reply_text(f"✅ Broadcast sent successfully to {sent} users.")

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    # Group restriction: Agar group me hai toh jab tak bot mention/reply na ho, tab tak ignore karega
    if chat.type in ["group", "supergroup"]:
        bot_user = context.bot.username
        is_mentioned = False
        
        if message.text or message.caption:
            text_content = message.text or message.caption
            if f"@{bot_user}" in text_content:
                is_mentioned = True
                
        if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
            is_mentioned = True
            
        if not is_mentioned:
            return  # Mention nahi kiya toh process nahi karega

    await save_user(user)

    media = message.document or message.video or message.audio or (message.photo[-1] if message.photo else None)
    if not media:
        return

    # Check file type category
    is_video_audio = bool(message.video or message.audio)
    
    filename = getattr(media, "file_name", None)
    if not filename:
        if message.photo:
            filename = f"Photo_{int(time.time())}.jpg"
        elif message.video:
            filename = f"Video_{int(time.time())}.mp4"
        elif message.audio:
            filename = f"Audio_{int(time.time())}.mp3"
        else:
            filename = f"Document_{int(time.time())}"

    original_caption = message.caption or ""
    profile_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    
    log_caption = (
        f"📁 <b>File Name:</b> <code>{filename}</code>\n"
        f"📝 <b>Caption:</b> {original_caption}\n\n"
        f"👤 <b>User:</b> <a href=\"{profile_url}\">{user.full_name}</a> (<code>{user.id}</code>)"
    )

    log_msg = await context.bot.copy_message(
        chat_id=LOG_GROUP,
        from_chat_id=chat.id,
        message_id=message.message_id,
        caption=log_caption,
        parse_mode=ParseMode.HTML
    )

    file_doc = {
        "msg_id": log_msg.message_id,
        "user_id": user.id,
        "filename": filename,
        "caption": original_caption,
        "created_at": int(time.time())
    }
    await files_col.insert_one(file_doc)

    watch_url = f"{BASE_URL}/watch?id={log_msg.message_id}"
    download_url = f"{BASE_URL}/stream?id={log_msg.message_id}&d=true"
    direct_url = f"{BASE_URL}/stream?id={log_msg.message_id}"

    raw_domain_path = BASE_URL.replace("https://", "").replace("http://", "")
    stream_path = f"{raw_domain_path}/stream?id={log_msg.message_id}"
    mx_intent_link = f"intent://{stream_path}#Intent;scheme=https;type=video/*;package=com.mxtech.videoplayer.ad;end"

    # Rich formatted response message with copyable block text links
    if is_video_audio:
        reply_text = (
            f"✨ <b>Media Links Generated Successfully!</b>\n\n"
            f"🎬 <b>File:</b> <code>{filename}</code>\n\n"
            f"<blockquote>📋 <b>Copyable Links (Tap to Copy):</b>\n"
            f"🔗 <b>Direct Stream URL:</b>\n<code>{direct_url}</code>\n\n"
            f"📥 <b>Instant Download URL:</b>\n<code>{download_url}</code>\n\n"
            f"📱 <b>MX Player Android Intent:</b>\n<code>{mx_intent_link}</code></blockquote>"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Play in MX Player", url=f"{BASE_URL}/mx?id={log_msg.message_id}", style="success")],
            [InlineKeyboardButton("🔵 Watch Web Player", url=watch_url, style="primary")],
            [
                InlineKeyboardButton("📥 Download", url=download_url, style="primary"),
                InlineKeyboardButton("🔗 Direct URL", url=direct_url, style="success")
            ]
        ])
    else:
        # Photos, Documents, Zips etc. -> No streaming option, only Download and Web view/preview
        reply_text = (
            f"📁 <b>Document/File Processed Successfully!</b>\n\n"
            f"📄 <b>File:</b> <code>{filename}</code>\n\n"
            f"<blockquote>📋 <b>Copyable Links (Tap to Copy):</b>\n"
            f"📥 <b>Download URL:</b>\n<code>{download_url}</code>\n\n"
            f"🌐 <b>Web View URL:</b>\n<code>{watch_url}</code></blockquote>"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download File", url=download_url, style="success")],
            [InlineKeyboardButton("🌐 Web View", url=watch_url, style="primary")]
        ])

    await message.reply_html(reply_text, reply_markup=buttons, disable_web_page_preview=True)

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
        return web.Response(text="File not found or expired", status=404)

    user_id = file_doc["user_id"]
    playlist_cursor = files_col.find({"user_id": user_id}).sort("created_at", -1).limit(10)
    playlist_html = ""

    async for item in playlist_cursor:
        active_class = "active" if item["msg_id"] == msg_id else ""
        item_url = f"{BASE_URL}/watch?id={item['msg_id']}"
        playlist_html += (
            f'<a href="{item_url}" class="playlist-item {active_class}">'
            f'<span>📁 {item["filename"]}</span>'
            f'<span>🔍 View</span>'
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

    raw_domain_path = BASE_URL.replace("https://", "").replace("http://", "")
    stream_path = f"{raw_domain_path}/stream?id={msg_id}"
    mx_ad_intent = f"intent://{stream_path}#Intent;scheme=https;type=video/*;package=com.mxtech.videoplayer.ad;end"

    return web.HTTPFound(mx_ad_intent)

async def handle_stream(request):
    msg_id_str = request.query.get("id")
    is_download = request.query.get("d") == "true"

    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File not found", status=404)

    msg = await tg_client.get_messages(LOG_GROUP, msg_id)
    media = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
    if not media:
        return web.Response(text="Media missing", status=404)

    filename = getattr(media, "file_name", file_doc["filename"])
    file_size = getattr(media, "file_size", 0)
    mime_type = getattr(media, "mime_type", "application/octet-stream")

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

async def init_app():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is Live!"))
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/mx", handle_mx_redirect)
    app.router.add_get("/stream", handle_stream)

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
