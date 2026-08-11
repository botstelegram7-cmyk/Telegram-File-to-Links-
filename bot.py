import time
import os
import math
import asyncio
import aiohttp
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import Update
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

# --- PYTHON TELEGRAM BOT APPLICATION (used only to receive/dispatch webhook updates) ---
ptb_app = Application.builder().token(BOT_TOKEN).build()

FSUB_CHANNEL = "serenaunzipbot"
FSUB_LINK = "https://t.me/serenaunzipbot"

# Streaming is served in 1 MiB chunks by Pyrogram's stream_media().
CHUNK_SIZE = 1024 * 1024

# --- HELPER: HUMAN READABLE FILE SIZE ---
def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "Unknown Size"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

# =========================================================================
# RAW TELEGRAM BOT API LAYER
# Every message the bot sends for /start and the Help panel goes straight
# to https://api.telegram.org/bot<token>/<method> over HTTP instead of
# going through PTB's high level wrappers.
# =========================================================================
API_ROOT = f"https://api.telegram.org/bot{BOT_TOKEN}"

async def tg_api(method: str, payload: dict):
    """Direct HTTP call to the Telegram Bot API. Drops empty keys before sending."""
    clean_payload = {k: v for k, v in payload.items() if v is not None}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_ROOT}/{method}", json=clean_payload) as resp:
            return await resp.json()

def btn(text, url=None, callback_data=None, style=None, web_app_url=None):
    """Build a single raw inline_keyboard button dict, with optional colour style.
    style: 'primary' (blue) | 'success' (green) | 'danger' (red)."""
    button = {"text": text}
    if url:
        button["url"] = url
    if callback_data:
        button["callback_data"] = callback_data
    if web_app_url:
        button["web_app"] = {"url": web_app_url}
    if style:
        button["style"] = style
    return button

def markup(rows):
    return {"inline_keyboard": rows}

async def send_message(chat_id, text, reply_markup=None, photo_url=None, disable_web_page_preview=True):
    if photo_url:
        return await tg_api("sendPhoto", {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        })
    return await tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_web_page_preview": disable_web_page_preview
    })

async def edit_message_text(chat_id, message_id, text, reply_markup=None):
    return await tg_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup
    })

async def delete_message(chat_id, message_id):
    return await tg_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

async def answer_callback_query(callback_query_id, text=None, show_alert=False):
    return await tg_api("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    })

# --- HTML TEMPLATES ---
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
            background: radial-gradient(circle at top, #131b2e 0%, #05070d 65%);
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
            max-width: 960px;
            background: #111827;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 18px;
            overflow: hidden;
            box-shadow: 0 25px 60px -12px rgba(0, 0, 0, 0.8);
        }}
        .video-container {{ width: 100%; background: #000; position: relative; }}
        .video-container video {{ width: 100%; max-height: 75vh; display: block; }}
        .plyr {{ --plyr-color-main: #6366f1; }}
        .loader {{
            position: absolute; inset: 0;
            display: flex; align-items: center; justify-content: center;
            background: #000; z-index: 5; pointer-events: none;
            transition: opacity 0.3s ease;
        }}
        .loader.hidden {{ opacity: 0; visibility: hidden; }}
        .spinner {{
            width: 46px; height: 46px;
            border: 4px solid rgba(255,255,255,0.15);
            border-top-color: #6366f1;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .info-panel {{ padding: 22px; display: flex; flex-direction: column; gap: 16px; }}
        .title {{ font-size: 1.25rem; font-weight: 700; color: #f1f5f9; word-break: break-all; }}
        .meta {{ font-size: 0.9rem; color: #94a3b8; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
        .badge {{
            background: rgba(99,102,241,0.15); color: #a5b4fc;
            padding: 3px 10px; border-radius: 20px; font-size: 0.78rem; font-weight: 600;
        }}
        .actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
        .btn {{
            padding: 14px 18px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 0.92rem;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            color: #fff;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        .btn:hover {{ transform: translateY(-2px); filter: brightness(1.08); }}
        .btn-orange {{ background: linear-gradient(135deg, #f97316, #ea580c); }}
        .btn-red {{ background: linear-gradient(135deg, #ef4444, #dc2626); }}
        .btn-green {{ background: linear-gradient(135deg, #10b981, #059669); }}
        .btn-blue {{ background: linear-gradient(135deg, #3b82f6, #2563eb); }}
        .footer-note {{ font-size: 0.78rem; color: #4b5563; text-align: center; margin-top: 4px; }}
    </style>
</head>
<body>
    <div class="player-wrapper">
        <div class="video-container">
            <div class="loader" id="loader"><div class="spinner"></div></div>
            <video id="player" playsinline controls crossorigin preload="metadata">
                <source src="{stream_url}" type="{mime_type}" />
            </video>
        </div>
        <div class="info-panel">
            <div class="title">🎬 {display_title}</div>
            <div class="meta">
                <span class="badge">💾 {file_size}</span>
                <span class="badge">⚡ Seekable Stream</span>
            </div>
            <div class="actions">
                <a href="{mx_url}" class="btn btn-orange">🟧 Open in MX Player</a>
                <a href="{vlc_url}" class="btn btn-red">🔴 Open in VLC</a>
                <a href="{download_url}" class="btn btn-green">📥 Instant Download</a>
                <button class="btn btn-blue" onclick="copyLink()">🔗 Copy Direct Link</button>
            </div>
            <div class="footer-note">Powered by File 2 Links — streamed directly from Telegram</div>
        </div>
    </div>
    <script src="https://cdn.plyr.io/3.7.8/plyr.polyfilled.js"></script>
    <script>
        const player = new Plyr('#player', {{
            settings: ['quality', 'speed'],
            seekTime: 10
        }});
        const loader = document.getElementById('loader');
        const videoEl = document.getElementById('player');
        videoEl.addEventListener('canplay', () => loader.classList.add('hidden'));
        videoEl.addEventListener('waiting', () => loader.classList.remove('hidden'));
        videoEl.addEventListener('playing', () => loader.classList.add('hidden'));
        function copyLink() {{
            navigator.clipboard.writeText("{stream_url}").then(() => {{
                const originalTitle = document.title;
                document.title = "✅ Link Copied!";
                setTimeout(() => document.title = originalTitle, 1500);
            }});
        }}
    </script>
</body>
</html>
"""

MX_REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Opening MX Player...</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #090d16; color: #f8fafc; font-family: 'Segoe UI', sans-serif;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; padding: 20px;
        }}
        .card {{
            background: #111827; border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px; padding: 34px 28px; max-width: 420px; width: 100%;
            text-align: center; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.7);
        }}
        .spinner {{
            width: 44px; height: 44px; margin: 0 auto 18px;
            border: 4px solid rgba(249,115,22,0.2); border-top-color: #f97316;
            border-radius: 50%; animation: spin 0.8s linear infinite;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        h2 {{ margin-bottom: 8px; }}
        p {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 22px; }}
        .btn {{
            display: inline-block; padding: 13px 26px;
            background: linear-gradient(135deg, #f97316, #ea580c); color: #fff;
            text-decoration: none; border-radius: 12px; font-weight: 700;
            box-shadow: 0 4px 12px rgba(249,115,22,0.4);
        }}
        .fallback {{ margin-top: 16px; font-size: 0.8rem; }}
        .fallback a {{ color: #60a5fa; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="spinner"></div>
        <h2>🟧 Opening in MX Player...</h2>
        <p>{display_title}</p>
        <a href="{intent_url}" id="mx-launch" class="btn">▶️ Open MX Player Now</a>
        <div class="fallback">Player didn't open? <a href="{stream_url}">Stream in browser</a> or <a href="{download_url}">download</a> instead.</div>
    </div>
    <script>
        // Auto-trigger the Android intent once the page loads. A visible
        // button is kept as a fallback for browsers that block auto intents.
        window.addEventListener('load', function () {{
            window.location.href = document.getElementById('mx-launch').href;
        }});
    </script>
</body>
</html>
"""

VLC_REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Opening VLC Player...</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #090d16; color: #f8fafc; font-family: 'Segoe UI', sans-serif;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; padding: 20px;
        }}
        .card {{
            background: #111827; border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px; padding: 34px 28px; max-width: 420px; width: 100%;
            text-align: center; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.7);
        }}
        .spinner {{
            width: 44px; height: 44px; margin: 0 auto 18px;
            border: 4px solid rgba(239,68,68,0.2); border-top-color: #ef4444;
            border-radius: 50%; animation: spin 0.8s linear infinite;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        h2 {{ margin-bottom: 8px; }}
        p {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 22px; }}
        .btn {{
            display: inline-block; padding: 13px 26px;
            background: linear-gradient(135deg, #ef4444, #dc2626); color: #fff;
            text-decoration: none; border-radius: 12px; font-weight: 700;
            box-shadow: 0 4px 12px rgba(239,68,68,0.4);
        }}
        .fallback {{ margin-top: 16px; font-size: 0.8rem; }}
        .fallback a {{ color: #60a5fa; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="spinner"></div>
        <h2>🔴 Opening in VLC Player...</h2>
        <p>{display_title}</p>
        <a href="{vlc_url}" id="vlc-launch" class="btn">▶️ Open VLC Player Now</a>
        <div class="fallback">Player didn't open? <a href="{stream_url}">Stream in browser</a> or <a href="{download_url}">download</a> instead.</div>
    </div>
    <script>
        window.addEventListener('load', function () {{
            window.location.href = document.getElementById('vlc-launch').href;
        }});
    </script>
</body>
</html>
"""

# =========================================================================
# TELEGRAM COMMAND / CALLBACK HANDLERS
# =========================================================================

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
        fsub_markup = markup([
            [btn("📢 Join Update Channel", url=FSUB_LINK, style="primary")],
            [btn("🔄 Try Again / Verify", callback_data="check_fsub", style="success")]
        ])
        await send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ <b>Access Restricted!</b>\n\nPlease join our channel to use <b>File 2 Links Bot</b>.",
            reply_markup=fsub_markup
        )
        return

    welcome_text = (
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        f"⚡ <b>File 2 Links Bot</b> is operational with <b>Integrated Player Support</b>.\n"
        f"<blockquote>Send any video, audio, photo, or document to generate instant Streaming & MX Player links.</blockquote>"
    )
    buttons = markup([
        [btn("✨ Open Web App", web_app_url=BASE_URL, style="primary")],
        [btn("📖 Help Guide", callback_data="help_menu", style="success")]
    ])

    await send_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=buttons,
        photo_url=START_PIC if START_PIC else None
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    message_id = query.message.message_id

    if query.data == "help_menu":
        await answer_callback_query(query.id)
        help_text = (
            "📖 <b>File 2 Links - Help Center</b>\n\n"
            "<blockquote>• Forward or send files in chat to get links.\n"
            "• Videos/Audio support direct streaming, MX Player & VLC.\n"
            "• Photos and documents provide secure web view & downloads.</blockquote>"
        )
        buttons = markup([
            [btn("❌ Close Panel", callback_data="close_menu", style="danger")]
        ])
        await edit_message_text(chat_id, message_id, help_text, reply_markup=buttons)

    elif query.data == "close_menu":
        await answer_callback_query(query.id)
        await delete_message(chat_id, message_id)

    elif query.data == "check_fsub":
        is_subscribed = await check_fsub(context.bot, query.from_user.id)
        if is_subscribed:
            await answer_callback_query(query.id)
            await delete_message(chat_id, message_id)
            await send_message(chat_id, "✅ Verified successfully! You can send your files now.")
        else:
            await answer_callback_query(query.id, text="❌ You haven't joined the channel yet!", show_alert=True)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    result = await files_col.delete_many({})
    await update.message.reply_text(f"🗑️ Cleared database records: <code>{result.deleted_count}</code>", parse_mode="HTML")

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
    await update.message.reply_text(f"✅ Broadcast complete. Delivered to <code>{sent}</code> users.", parse_mode="HTML")

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
            fsub_markup = markup([
                [btn("📢 Join Update Channel", url=FSUB_LINK, style="primary")],
                [btn("🔄 Verify Membership", callback_data="check_fsub", style="success")]
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
        parse_mode="HTML"
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

    reply_text = (
        f"⚡ <b>File Stream & Download Ready!</b>\n\n"
        f"📁 <b>File Name:</b> <code>{filename}</code>\n"
        f"💾 <b>File Size:</b> <code>{file_size_str}</code>\n\n"
        f"<blockquote>🔗 <b>Direct Link:</b>\n<code>{direct_url}</code></blockquote>"
    )

    if is_video_audio:
        buttons = markup([
            [
                btn("🖥️ Watch Web View", url=watch_url, style="primary"),
                btn("📥 Download", url=download_url, style="success")
            ],
            [
                btn("🟧 MX Player", url=mx_url, style="danger"),
                btn("🔴 VLC Player", url=vlc_url, style="danger")
            ]
        ])
    else:
        buttons = markup([
            [btn("🌐 Web View Preview", url=watch_url, style="primary")],
            [btn("📥 Fast Download File", url=download_url, style="success")]
        ])

    await message.reply_html(reply_text, reply_markup=buttons, disable_web_page_preview=True)

# =========================================================================
# HTTP RANGE PARSING (needed for seeking / MX Player / VLC support)
# =========================================================================
def parse_range_header(range_header: str, file_size: int):
    range_val = range_header.strip().lower().replace("bytes=", "").split("-")
    from_bytes = int(range_val[0]) if range_val[0].strip() else 0
    until_bytes = int(range_val[1]) if len(range_val) > 1 and range_val[1].strip() else file_size - 1
    if until_bytes >= file_size:
        until_bytes = file_size - 1
    if from_bytes < 0 or from_bytes > until_bytes:
        from_bytes = 0
    return from_bytes, until_bytes

# --- INSTANT HTML REDIRECT HANDLERS FOR MX PLAYER & VLC ---
async def handle_mx(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    display_title = file_doc.get("display_title", "Media Stream") if file_doc else "Media Stream"

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"
    # 'type=video/*' + browser_fallback_url makes the intent reliable across
    # MX Player builds and gives Chrome/Android something sane to fall back to.
    intent_url = (
        f"intent:{stream_url}#Intent;"
        f"package=com.mxtech.videoplayer.ad;"
        f"type=video/*;"
        f"S.title={display_title};"
        f"S.browser_fallback_url={stream_url};"
        f"end"
    )

    html_content = MX_REDIRECT_TEMPLATE.format(
        display_title=display_title,
        intent_url=intent_url,
        stream_url=stream_url,
        download_url=download_url
    )
    return web.Response(text=html_content, content_type="text/html")

async def handle_vlc(request):
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    display_title = file_doc.get("display_title", "Media Stream") if file_doc else "Media Stream"

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"
    vlc_url = f"vlc://{stream_url}"

    html_content = VLC_REDIRECT_TEMPLATE.format(
        display_title=display_title,
        vlc_url=vlc_url,
        stream_url=stream_url,
        download_url=download_url
    )
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
    mx_url = f"{BASE_URL}/mx?id={msg_id}"
    vlc_url = f"{BASE_URL}/vlc?id={msg_id}"

    display_title = file_doc.get("display_title", "Media File")
    file_size_str = file_doc.get("file_size_str", "Unknown Size")

    if file_doc.get("is_video_audio", False):
        html_content = VIDEO_PLAYER_TEMPLATE.format(
            stream_url=stream_url,
            download_url=download_url,
            mx_url=mx_url,
            vlc_url=vlc_url,
            display_title=display_title,
            file_size=file_size_str,
            mime_type="video/mp4"
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
    """Serves the media binary with proper HTTP Range support. Without this,
    players like MX Player / VLC / the in-browser <video> tag cannot seek and
    often refuse to start playback at all on larger files."""
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

        range_header = request.headers.get("Range")

        if range_header and file_size:
            from_bytes, until_bytes = parse_range_header(range_header, file_size)
            status = 206
        else:
            from_bytes, until_bytes = 0, (file_size - 1 if file_size else 0)
            status = 200

        req_length = until_bytes - from_bytes + 1

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(req_length),
            "Accept-Ranges": "bytes",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

        if is_download:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        else:
            headers["Content-Disposition"] = f'inline; filename="{filename}"'

        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)

        if not file_size:
            # Fallback for media without a known size: stream sequentially, no ranges.
            async for chunk in tg_client.stream_media(msg):
                try:
                    await response.write(chunk)
                except (ConnectionResetError, RuntimeError):
                    break
            return response

        offset = from_bytes - (from_bytes % CHUNK_SIZE)
        first_part_cut = from_bytes - offset
        last_part_cut = (until_bytes % CHUNK_SIZE) + 1
        part_count = math.ceil((until_bytes + 1 - offset) / CHUNK_SIZE)

        current_part = 0
        async for chunk in tg_client.stream_media(msg, offset=offset // CHUNK_SIZE, limit=part_count):
            current_part += 1
            if part_count == 1:
                chunk = chunk[first_part_cut:last_part_cut]
            elif current_part == 1:
                chunk = chunk[first_part_cut:]
            elif current_part == part_count:
                chunk = chunk[:last_part_cut]
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
