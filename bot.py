import time
import os
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters as ptb_filters, ContextTypes
from pyrogram import Client
import asyncio

# Import config – handle SESSION_STRING missing gracefully
from config import (
    BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, ADMIN_ID,
    PORT, MONGO_URI, DB_NAME, AUTO_DELETE_TIME, START_PIC, BASE_URL
)
try:
    from config import SESSION_STRING
except ImportError:
    SESSION_STRING = ""

# If SESSION_STRING is "0" or empty, treat as not set
if not SESSION_STRING or SESSION_STRING == "0":
    SESSION_STRING = ""

# --- MONGODB ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
files_col = db["stream_files"]
users_col = db["users"]

# --- PYROGRAM CLIENT (with session string support) ---
if SESSION_STRING:
    tg_client = Client(
        "File2LinksSession",
        session_string=SESSION_STRING,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True
    )
    print("✅ Using persistent session string.")
else:
    tg_client = Client(
        "File2LinksSession",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True
    )
    print("ℹ️ Using bot token for Pyrogram (may cause flood waits on repeated starts).")

pyrogram_ready = False  # flag to indicate client is ready

# --- PTB APP ---
ptb_app = Application.builder().token(BOT_TOKEN).build()

FSUB_CHANNEL = "serenaunzipbot"
FSUB_LINK = "https://t.me/serenaunzipbot"

# --- HTML TEMPLATES (unchanged) ---
GENERIC_WEB_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{display_title} - File 2 Links Viewer</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
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
        }}
        .card {{
            background: #111827;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 30px;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
            text-align: center;
        }}
        .icon {{ font-size: 48px; margin-bottom: 15px; }}
        .title {{ font-size: 1.2rem; font-weight: 600; color: #e2e8f0; margin-bottom: 20px; word-break: break-all; }}
        .preview-container {{ margin: 20px 0; max-height: 350px; overflow: hidden; border-radius: 10px; }}
        .preview-container img {{ width: 100%; height: auto; object-fit: contain; }}
        .btn {{
            display: inline-block;
            width: 100%;
            padding: 12px;
            background: #2563eb;
            color: #fff;
            font-weight: 600;
            border-radius: 10px;
            text-decoration: none;
            transition: background 0.2s;
            margin-top: 10px;
        }}
        .btn:hover {{ background: #1d4ed8; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon_emoji}</div>
        <div class="title">{display_title}</div>
        {media_preview_html}
        <a href="{download_url}" class="btn">📥 Download File Securely</a>
    </div>
</body>
</html>
"""

VIDEO_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{display_title} - File 2 Links Stream</title>
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
        }}
        .player-wrapper {{
            width: 100%;
            max-width: 900px;
            background: #111827;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
        }}
        .video-container {{ width: 100%; background: #000; }}
        video {{ width: 100%; max-height: 75vh; }}
        .info-panel {{ padding: 20px; display: flex; flex-direction: column; gap: 15px; }}
        .title {{ font-size: 1.2rem; font-weight: 700; color: #e2e8f0; word-break: break-all; }}
        .actions {{ display: flex; gap: 12px; flex-wrap: wrap; }}
        .btn {{
            padding: 10px 20px;
            border-radius: 10px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: #2563eb;
            color: #fff;
            border: none;
        }}
        .btn:hover {{ background: #1d4ed8; }}
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
            <div class="title">🎬 {display_title}</div>
            <div class="actions">
                <a href="{download_url}" class="btn">📥 Instant Download</a>
            </div>
        </div>
    </div>
    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>const player = new Plyr('#player');</script>
</body>
</html>
"""

# --- HELPER FUNCTIONS (unchanged) ---
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

async def check_fsub(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=f"@{FSUB_CHANNEL}", user_id=user_id)
        if member.status in ["left", "kicked"]:
            return False
        return True
    except Exception:
        return False

async def save_user(user):
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {"name": user.full_name, "username": user.username, "last_seen": int(time.time())}},
        upsert=True
    )

# --- COMMAND HANDLERS (unchanged) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)

    if update.effective_chat.type != "private":
        return

    is_subscribed = await check_fsub(context.bot, user.id)
    if not is_subscribed:
        fsub_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Update Channel", url=FSUB_LINK)],
            [InlineKeyboardButton("🔄 Try Again / Verify", callback_data="check_fsub")]
        ])
        await update.message.reply_text(
            "⚠️ <b>Access Restricted!</b>\n\n"
            "Please join our channel to use <b>File 2 Links Bot</b>.",
            reply_markup=fsub_markup,
            parse_mode=ParseMode.HTML
        )
        return

    welcome_text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"⚡ <b>File 2 Links Bot</b> is operational.\n"
        f"<blockquote>Send any media file to generate fast distribution links.</blockquote>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Open Web App", web_app={"url": f"{BASE_URL}"})],
        [InlineKeyboardButton("📖 Help Guide", callback_data="help_menu")]
    ])

    await send_raw_telegram_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=buttons.to_dict(),
        photo_url=START_PIC if START_PIC else None
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "help_menu":
        help_text = (
            "📖 <b>File 2 Links - Help Center</b>\n\n"
            "<blockquote>• Forward or send files in chat to get links.\n"
            "• Videos/Audio support direct streaming.\n"
            "• Photos and documents provide secure web view & downloads.</blockquote>"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Close Panel", callback_data="close_menu")]
        ])
        await query.message.edit_text(help_text, reply_markup=buttons, parse_mode=ParseMode.HTML)
    elif query.data == "close_menu":
        await query.message.delete()
    elif query.data == "check_fsub":
        is_subscribed = await check_fsub(context.bot, query.from_user.id)
        if is_subscribed:
            await query.message.delete()
            await query.message.reply_text("✅ Verified successfully! You can send your files now.")
        else:
            await query.answer("❌ You haven't joined the channel yet!", show_alert=True)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    result = await files_col.delete_many({})
    await update.message.reply_text(f"🗑️ Cleared database records: <code>{result.deleted_count}</code>", parse_mode=ParseMode.HTML)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to an announcement message to broadcast.")
        return

    sent = 0
    async for user_doc in users_col.find():
        try:
            await context.bot.copy_message(chat_id=user_doc["_id"], from_chat_id=update.effective_chat.id, message_id=update.message.reply_to_message.message_id)
            sent += 1
        except Exception:
            continue
    await update.message.reply_text(f"✅ Broadcast complete. Delivered to <code>{sent}</code> users.", parse_mode=ParseMode.HTML)

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ["group", "supergroup"]:
        bot_username = context.bot.username
        is_mentioned = False
        if (message.text or message.caption) and f"@{bot_username}" in (message.text or message.caption):
            is_mentioned = True
        if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
            is_mentioned = True
        if not is_mentioned:
            return

    if chat.type == "private":
        if not await check_fsub(context.bot, user.id):
            fsub_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Update Channel", url=FSUB_LINK)],
                [InlineKeyboardButton("🔄 Verify Membership", callback_data="check_fsub")]
            ])
            await message.reply_text("⚠️ Please join our official channel to process links.", reply_markup=fsub_markup)
            return

    await save_user(user)

    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    original_caption = message.caption or ""
    
    if message.photo:
        filename = f"Image_{int(time.time())}.jpg"
    else:
        filename = getattr(media, "file_name", None)
        if not filename:
            if message.video:
                filename = f"Video_{int(time.time())}.mp4"
            elif message.audio:
                filename = f"Audio_{int(time.time())}.mp3"
            else:
                filename = f"Document_{int(time.time())}"

    display_title = original_caption if original_caption.strip() else filename
    is_video_audio = bool(message.video or message.audio)

    log_caption = (
        f"📁 <b>Title:</b> <code>{display_title}</code>\n\n"
        f"👤 <b>Uploader:</b> <a href=\"t.me/{user.username}\">{user.full_name}</a> (<code>{user.id}</code>)"
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
        "display_title": display_title,
        "is_video_audio": is_video_audio,
        "created_at": int(time.time())
    }
    await files_col.insert_one(file_doc)

    watch_url = f"{BASE_URL}/watch?id={log_msg.message_id}"
    download_url = f"{BASE_URL}/stream?id={log_msg.message_id}&d=true"
    direct_url = f"{BASE_URL}/stream?id={log_msg.message_id}"

    if chat.type == "private":
        if is_video_audio:
            reply_text = f"<code>{direct_url}</code>\n\n<code>{download_url}</code>"
        else:
            reply_text = f"<code>{download_url}</code>\n\n<code>{watch_url}</code>"
    else:
        reply_text = (
            f"✨ <b>Links Generated!</b>\n\n"
            f"📌 <b>Title:</b> <code>{display_title}</code>\n\n"
            f"<blockquote>📥 <b>Download Link:</b>\n<code>{download_url}</code></blockquote>"
        )

    if is_video_audio:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Stream / Web View", url=watch_url)],
            [InlineKeyboardButton("📥 Download File", url=download_url)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Web View Preview", url=watch_url)],
            [InlineKeyboardButton("📥 Download File", url=download_url)]
        ])

    await message.reply_html(reply_text, reply_markup=buttons, disable_web_page_preview=True)

# --- WEB HANDLERS (with readiness check) ---
async def handle_watch(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File content not found or expired.", status=404)

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"
    display_title = file_doc.get("display_title", "Media File")

    if file_doc.get("is_video_audio", False):
        html_content = VIDEO_PLAYER_TEMPLATE.format(
            stream_url=stream_url,
            download_url=download_url,
            display_title=display_title
        )
    else:
        is_image = "Image" in display_title or ".jpg" in display_title.lower() or ".png" in display_title.lower() or ".jpeg" in display_title.lower()
        icon_emoji = "🖼️" if is_image else "📄"
        media_preview_html = f'<div class="preview-container"><img src="{stream_url}" alt="Preview"></div>' if is_image else ""
        
        html_content = GENERIC_WEB_TEMPLATE.format(
            display_title=display_title,
            icon_emoji=icon_emoji,
            download_url=download_url,
            media_preview_html=media_preview_html
        )

    return web.Response(text=html_content, content_type="text/html")

async def handle_stream(request):
    global pyrogram_ready
    if not pyrogram_ready:
        return web.Response(text="Pyrogram client is not ready yet. Please try again later.", status=503)

    msg_id_str = request.query.get("id")
    is_download = request.query.get("d") == "true"

    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File record missing", status=404)

    try:
        msg = await tg_client.get_messages(LOG_GROUP, msg_id)
        media = msg.document or msg.video or msg.audio or msg.photo
        if not media:
            return web.Response(text="Underlying media binary missing", status=404)

        if message_photo := msg.photo:
            filename = f"Image_{msg_id}.jpg"
            file_size = getattr(message_photo, "file_size", 0)
            mime_type = "image/jpeg"
        else:
            filename = getattr(media, "file_name", file_doc.get("filename", "download"))
            file_size = getattr(media, "file_size", 0)
            mime_type = getattr(media, "mime_type", "application/octet-stream")

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes"
        }
        if is_download:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        else:
            headers["Content-Disposition"] = f'inline; filename="{filename}"'

        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        async for chunk in tg_client.stream_media(msg):
            try:
                await response.write(chunk)
            except (ConnectionResetError, RuntimeError):
                break

        return response
    except Exception as e:
        return web.Response(text=f"Stream Error: {str(e)}", status=500)

# --- WEBHOOK HANDLER ---
async def webhook_handler(request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        print(f"Webhook error: {e}")
        return web.Response(status=500)

# --- REGISTER HANDLERS ---
ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(CommandHandler("clear", clear_command))
ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
ptb_app.add_handler(CallbackQueryHandler(help_callback))
ptb_app.add_handler(MessageHandler(
    ptb_filters.Document.ALL | ptb_filters.VIDEO | ptb_filters.AUDIO | ptb_filters.PHOTO,
    media_handler
))

# --- BACKGROUND TASK TO START PYROGRAM ---
async def start_pyrogram():
    global pyrogram_ready
    max_retries = 5
    retry_delay = 60  # seconds
    for attempt in range(1, max_retries + 1):
        try:
            await tg_client.start()
            pyrogram_ready = True
            print("✅ Pyrogram client started successfully.")
            return
        except Exception as e:
            print(f"⚠️ Pyrogram start attempt {attempt} failed: {e}")
            if attempt < max_retries:
                print(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                print("❌ Failed to start Pyrogram after multiple attempts.")

# --- AIOHTTP APP SETUP ---
async def init_app():
    app = web.Application()
    
    # Routes
    app.router.add_get("/", lambda r: web.Response(text="File 2 Links Production Server Online."))
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/stream", handle_stream)
    app.router.add_post(f"/{BOT_TOKEN}", webhook_handler)

    async def on_startup(app):
        # Start PTB
        await ptb_app.initialize()
        await ptb_app.start()
        # Set webhook
        await ptb_app.bot.set_webhook(url=f"{BASE_URL}/{BOT_TOKEN}")
        print("✅ PTB started and webhook set.")
        # Start Pyrogram in background
        asyncio.create_task(start_pyrogram())

    async def on_shutdown(app):
        await ptb_app.bot.delete_webhook()
        await ptb_app.stop()
        await ptb_app.shutdown()
        if pyrogram_ready:
            await tg_client.stop()
        mongo_client.close()
        print("🛑 Shutdown complete.")

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

if __name__ == "__main__":
    app = init_app()
    web.run_app(app, host="0.0.0.0", port=PORT)