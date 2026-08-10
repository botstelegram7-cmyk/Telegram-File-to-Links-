import time
import os
import asyncio
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters as ptb_filters, ContextTypes
from pyrogram import Client

from config import (
    BOT_TOKEN, API_ID, API_HASH, LOG_GROUP, ADMIN_ID,
    PORT, MONGO_URI, DB_NAME, START_PIC, BASE_URL
)

# --- MONGODB CONFIGURATION ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]
files_col = db["stream_files"]
users_col = db["users"]

# --- PYROGRAM CLIENT FOR MEDIA STREAMING ---
tg_client = Client(
    "File2LinksSession",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

# --- PYTHON TELEGRAM BOT APPLICATION ---
ptb_app = Application.builder().token(BOT_TOKEN).build()

FSUB_CHANNEL = "serenaunzipbot"
FSUB_LINK = "https://t.me/serenaunzipbot"

# --- HELPER: HUMAN READABLE FILE SIZE ---
def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "Unknown Size"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

# --- HTML TEMPLATES WITH VIBRANT COLORED BUTTONS ---
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
        .icon {{ font-size: 52px; margin-bottom: 15px; }}
        .title {{ font-size: 1.25rem; font-weight: 700; color: #f1f5f9; margin-bottom: 8px; word-break: break-all; }}
        .meta {{ font-size: 0.9rem; color: #94a3b8; margin-bottom: 20px; }}
        .preview-container {{ margin: 20px 0; max-height: 350px; overflow: hidden; border-radius: 12px; }}
        .preview-container img {{ width: 100%; height: auto; object-fit: contain; }}
        .btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            width: 100%;
            padding: 14px;
            color: #fff;
            font-weight: 600;
            border-radius: 12px;
            text-decoration: none;
            transition: all 0.2s ease;
            margin-top: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        .btn-green {{ background: linear-gradient(135deg, #10b981, #059669); }}
        .btn-green:hover {{ background: linear-gradient(135deg, #059669, #047857); transform: translateY(-1px); }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon_emoji}</div>
        <div class="title">{display_title}</div>
        <div class="meta">💾 Size: {file_size}</div>
        {media_preview_html}
        <a href="{download_url}" class="btn btn-green">📥 Download File Securely</a>
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
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
        }}
        .video-container {{ width: 100%; background: #000; }}
        video {{ width: 100%; max-height: 75vh; }}
        .info-panel {{ padding: 22px; display: flex; flex-direction: column; gap: 18px; }}
        .title {{ font-size: 1.3rem; font-weight: 700; color: #f1f5f9; word-break: break-all; }}
        .meta {{ font-size: 0.95rem; color: #94a3b8; }}
        .actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
        .btn {{
            padding: 14px 20px;
            border-radius: 12px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            color: #fff;
            border: none;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        .btn:hover {{ transform: translateY(-2px); }}
        .btn-orange {{ background: linear-gradient(135deg, #f97316, #ea580c); }}
        .btn-red {{ background: linear-gradient(135deg, #ef4444, #dc2626); }}
        .btn-green {{ background: linear-gradient(135deg, #10b981, #059669); }}
        .btn-blue {{ background: linear-gradient(135deg, #3b82f6, #2563eb); }}
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
            <div class="meta">💾 File Size: {file_size}</div>
            <div class="actions">
                <a href="{mx_url}" class="btn btn-orange">🟧 Open in MX Player</a>
                <a href="{vlc_url}" class="btn btn-red">🔴 Open in VLC</a>
                <a href="{download_url}" class="btn btn-green">📥 Instant Download</a>
            </div>
        </div>
    </div>
    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>const player = new Plyr('#player');</script>
</body>
</html>
"""

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
            "⚠️ <b>Access Restricted!</b>\n\nPlease join our channel to use <b>File 2 Links Bot</b>.",
            reply_markup=fsub_markup,
            parse_mode=ParseMode.HTML
        )
        return

    welcome_text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"⚡ <b>File 2 Links Bot</b> is operational with <b>Integrated Player Support</b>.\n"
        f"<blockquote>Send any video, audio, photo, or document to generate instant Streaming & MX Player links.</blockquote>"
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
            "• Videos/Audio support direct streaming, MX Player & VLC.\n"
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
    file_size_bytes = getattr(media, "file_size", 0)
    file_size_str = get_readable_file_size(file_size_bytes)
    
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
        f"📁 <b>Title:</b> <code>{display_title}</code>\n"
        f"💾 <b>Size:</b> <code>{file_size_str}</code>\n\n"
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
        "file_size_str": file_size_str,
        "is_video_audio": is_video_audio,
        "created_at": int(time.time())
    }
    await files_col.insert_one(file_doc)

    watch_url = f"{BASE_URL}/watch?id={log_msg.message_id}"
    download_url = f"{BASE_URL}/stream?id={log_msg.message_id}&d=true"
    direct_url = f"{BASE_URL}/stream?id={log_msg.message_id}"
    mx_url = f"{BASE_URL}/mx?id={log_msg.message_id}"
    vlc_url = f"{BASE_URL}/vlc?id={log_msg.message_id}"

    # RICH FORMATTED TELEGRAM MESSAGE
    reply_text = (
        f"⚡ <b>File Stream & Download Ready!</b>\n\n"
        f"📁 <b>File Name:</b> <code>{filename}</code>\n"
        f"💾 <b>File Size:</b> <code>{file_size_str}</code>\n\n"
        f"<blockquote>🔗 <b>Direct Link:</b>\n<code>{direct_url}</code></blockquote>"
    )

    if is_video_audio:
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🖥️ Watch Web View", url=watch_url),
                InlineKeyboardButton("📥 Download", url=download_url)
            ],
            [
                InlineKeyboardButton("🟧 MX Player", url=mx_url),
                InlineKeyboardButton("🔴 VLC Player", url=vlc_url)
            ]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Web View Preview", url=watch_url)],
            [InlineKeyboardButton("📥 Fast Download File", url=download_url)]
        ])

    await message.reply_html(reply_text, reply_markup=buttons, disable_web_page_preview=True)

# --- INSTANT HTML REDIRECT HANDLERS FOR MX PLAYER & VLC ---
async def handle_mx(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    display_title = file_doc.get("display_title", "Media Stream") if file_doc else "Media Stream"
    
    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    intent_url = f"intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;S.title={display_title};end"
    
    html_content = f"""<!DOCTYPE html>
    <html><head>
        <meta charset="UTF-8">
        <title>Opening MX Player...</title>
        <meta http-equiv="refresh" content="0;url={intent_url}">
        <style>
            body {{ background: #090d16; color: #f8fafc; font-family: sans-serif; text-align: center; padding: 50px 20px; }}
            .btn {{ display: inline-block; margin-top: 20px; padding: 12px 25px; background: #f97316; color: #fff; text-decoration: none; border-radius: 10px; font-weight: bold; }}
        </style>
    </head><body>
        <h2>🟧 Opening in MX Player...</h2>
        <p>If MX Player does not open automatically, click the button below:</p>
        <a href="{intent_url}" class="btn">▶️ Open MX Player Now</a>
    </body></html>"""
    return web.Response(text=html_content, content_type="text/html")

async def handle_vlc(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    vlc_url = f"vlc://{stream_url}"
    
    html_content = f"""<!DOCTYPE html>
    <html><head>
        <meta charset="UTF-8">
        <title>Opening VLC Player...</title>
        <meta http-equiv="refresh" content="0;url={vlc_url}">
        <style>
            body {{ background: #090d16; color: #f8fafc; font-family: sans-serif; text-align: center; padding: 50px 20px; }}
            .btn {{ display: inline-block; margin-top: 20px; padding: 12px 25px; background: #ef4444; color: #fff; text-decoration: none; border-radius: 10px; font-weight: bold; }}
        </style>
    </head><body>
        <h2>🔴 Opening in VLC Player...</h2>
        <p>If VLC Player does not open automatically, click the button below:</p>
        <a href="{vlc_url}" class="btn">▶️ Open VLC Player Now</a>
    </body></html>"""
    return web.Response(text=html_content, content_type="text/html")

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
    mx_url = f"intent:{stream_url}#Intent;package=com.mxtech.videoplayer.ad;S.title={file_doc.get('display_title', 'Video')};end"
    vlc_url = f"vlc://{stream_url}"
    
    display_title = file_doc.get("display_title", "Media File")
    file_size_str = file_doc.get("file_size_str", "Unknown Size")

    if file_doc.get("is_video_audio", False):
        html_content = VIDEO_PLAYER_TEMPLATE.format(
            stream_url=stream_url,
            download_url=download_url,
            mx_url=mx_url,
            vlc_url=vlc_url,
            display_title=display_title,
            file_size=file_size_str
        )
    else:
        is_image = "Image" in display_title or ".jpg" in display_title.lower() or ".png" in display_title.lower() or ".jpeg" in display_title.lower()
        icon_emoji = "🖼️" if is_image else "📄"
        media_preview_html = f'<div class="preview-container"><img src="{stream_url}" alt="Preview"></div>' if is_image else ""
        
        html_content = GENERIC_WEB_TEMPLATE.format(
            display_title=display_title,
            file_size=file_size_str,
            icon_emoji=icon_emoji,
            download_url=download_url,
            media_preview_html=media_preview_html
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

# --- SECURE ASYNCHRONOUS WEBHOOK WRAPPER ---
async def webhook_handler(request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.update_queue.put(update)
        return web.Response(text="OK")
    except Exception as e:
        return web.Response(text=f"Error: {str(e)}", status=500)

ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(CommandHandler("clear", clear_command))
ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
ptb_app.add_handler(CallbackQueryHandler(help_callback))
ptb_app.add_handler(MessageHandler(
    ptb_filters.Document.ALL | ptb_filters.VIDEO | ptb_filters.AUDIO | ptb_filters.PHOTO,
    media_handler
))

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="File 2 Links Production Server Online."))
    app.router.add_get("/watch", handle_watch)
    app.router.add_get("/stream", handle_stream)
    app.router.add_get("/mx", handle_mx)
    app.router.add_get("/vlc", handle_vlc)
    
    if BASE_URL:
        app.router.add_post(f"/{BOT_TOKEN}", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await ptb_app.initialize()
    await ptb_app.start()
    await tg_client.start()

    if BASE_URL:
        webhook_url = f"{BASE_URL}/{BOT_TOKEN}"
        await ptb_app.bot.set_webhook(url=webhook_url)

    print(f"🚀 Service fully running on port {PORT} and loop synchronized!")

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
