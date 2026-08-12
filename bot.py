# =========================================================================
#  File 2 Links — Telegram File-to-Link Streaming Bot
#  -------------------------------------------------------------------
#  Telegram : @Xioqui_xin (Xioqui)  ·  @TechnicalSerena (Technical 🕷️ Serena)
#  Instagram: @Prince572002 (Alka Music Status)
#  Please keep this credit block intact if you fork or redistribute.
#  See LICENSE for usage terms and liability disclaimer.
# =========================================================================
import time
import os
import math
import mimetypes
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

CREDIT_LINE = "Telegram @Xioqui_xin · @TechnicalSerena  |  Instagram @Prince572002"

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

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".3gp", ".ts", ".vob", ".mpg", ".mpeg"}
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".alac"}

# --- HELPER: HUMAN READABLE FILE SIZE ---
def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "Unknown Size"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} TB"

def guess_mime_type(filename, telegram_mime):
    """Best-effort mime detection so ANY audio/video format Telegram gives us
    (mp4, mkv, webm, mov, avi, mp3, flac, ogg, wav, m4a, opus, etc.) gets a
    sane Content-Type instead of falling back to a hardcoded guess."""
    if telegram_mime and telegram_mime != "application/octet-stream":
        return telegram_mime
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video/mp4"
    if ext in AUDIO_EXTENSIONS:
        return "audio/mpeg"
    return "application/octet-stream"

def classify_media_kind(filename, mime_type):
    """Returns ('video'|'audio'|None) for files sent as generic Documents —
    e.g. MKV files, which Telegram usually delivers as a Document rather
    than its native 'video' type, so they need extension/mime sniffing to
    still unlock streaming, MX Player, and VLC buttons."""
    if mime_type and mime_type.startswith("video/"):
        return "video"
    if mime_type and mime_type.startswith("audio/"):
        return "audio"
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return None

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

VIDEO_MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp"}
GIF_EXTS = {".gif"}

async def send_start_media(chat_id, text, reply_markup, media_url):
    """Sends the /start banner via the correct raw Bot API method for whatever
    media type START_PIC actually is (photo, video, or animated gif) — a plain
    sendPhoto call on a video URL delivers nothing, which is why a 15s mp4
    banner was showing up blank."""
    if not media_url:
        return await send_message(chat_id, text, reply_markup)

    ext = os.path.splitext(media_url.split("?")[0])[1].lower()
    if ext in GIF_EXTS:
        method, field = "sendAnimation", "animation"
    elif ext in VIDEO_MEDIA_EXTS:
        method, field = "sendVideo", "video"
    else:
        method, field = "sendPhoto", "photo"

    return await tg_api(method, {
        "chat_id": chat_id,
        field: media_url,
        "caption": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup
    })

async def send_message(chat_id, text, reply_markup=None, disable_web_page_preview=True):
    return await tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
        "disable_web_page_preview": disable_web_page_preview
    })

async def edit_message(chat_id, message_id, text, reply_markup=None, has_media=False):
    """editMessageText only works on plain text messages. If the original
    message carries a photo/video/animation/document/audio, Telegram rejects
    it with 'There is no text in the message to edit' — editMessageCaption
    must be used instead. This is why the Help button was silently failing
    whenever /start was sent with a START_PIC attached."""
    if has_media:
        return await tg_api("editMessageCaption", {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        })
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

# =========================================================================
# SHARED THEME SWITCHER (CSS + HTML + JS) — reused by the video player and
# the library page. Sits in a fixed corner, offers several light palettes
# plus the default dark one, and drives the page background/text colours
# via CSS variables.
#
# NOTE: every literal brace below is doubled ({{ }}) because these blocks
# get embedded into templates that are rendered with str.format(). If a
# template is returned WITHOUT calling .format() on it, those doubled
# braces stay literally doubled in the output HTML and break the inline
# JavaScript (this was the exact cause of the blank "Loading your
# library…" page — always call .format() on the final template, even with
# no keyword arguments, so the doubled braces collapse correctly).
# =========================================================================
THEME_STYLE = """
:root {{
    --bg-page:#05070d; --bg-page-2:#131b2e; --bg-panel:#111827; --bg-card:#111827;
    --text-primary:#f8fafc; --text-secondary:#94a3b8; --border-color:rgba(255,255,255,0.08);
    --badge-bg:rgba(99,102,241,0.15); --badge-text:#a5b4fc;
}}
body[data-theme="daylight"] {{
    --bg-page:#eef1f6; --bg-page-2:#f7f9fc; --bg-panel:#ffffff; --bg-card:#ffffff;
    --text-primary:#0f172a; --text-secondary:#475569; --border-color:rgba(15,23,42,0.08);
    --badge-bg:rgba(79,70,229,0.1); --badge-text:#4f46e5;
}}
body[data-theme="cream"] {{
    --bg-page:#faf6ef; --bg-page-2:#fdfbf7; --bg-panel:#fffaf2; --bg-card:#fffaf2;
    --text-primary:#4a3728; --text-secondary:#8a7361; --border-color:rgba(74,55,40,0.1);
    --badge-bg:rgba(217,119,6,0.12); --badge-text:#b45309;
}}
body[data-theme="ocean"] {{
    --bg-page:#eaf4fb; --bg-page-2:#f5faff; --bg-panel:#ffffff; --bg-card:#ffffff;
    --text-primary:#0b3559; --text-secondary:#4f7292; --border-color:rgba(11,53,89,0.1);
    --badge-bg:rgba(14,116,183,0.12); --badge-text:#0e74b7;
}}
body[data-theme="mint"] {{
    --bg-page:#eafaf3; --bg-page-2:#f5fdf9; --bg-panel:#ffffff; --bg-card:#ffffff;
    --text-primary:#0f3d2c; --text-secondary:#4f8a72; --border-color:rgba(15,61,44,0.1);
    --badge-bg:rgba(5,150,105,0.12); --badge-text:#059669;
}}
body[data-theme="rose"] {{
    --bg-page:#fdf1f5; --bg-page-2:#fff7f9; --bg-panel:#ffffff; --bg-card:#ffffff;
    --text-primary:#5c1f34; --text-secondary:#a1667c; --border-color:rgba(92,31,52,0.1);
    --badge-bg:rgba(219,39,119,0.12); --badge-text:#db2777;
}}
.theme-fab {{
    position:fixed; top:16px; right:16px; z-index:50; width:44px; height:44px; border-radius:50%;
    background:var(--bg-panel); border:1px solid var(--border-color); color:var(--text-primary);
    display:flex; align-items:center; justify-content:center; cursor:pointer; box-shadow:0 6px 18px rgba(0,0,0,.25);
}}
.theme-fab svg {{ width:20px; height:20px; }}
.theme-panel {{
    position:fixed; top:66px; right:16px; z-index:50; background:var(--bg-panel);
    border:1px solid var(--border-color); border-radius:14px; padding:14px; display:none;
    box-shadow:0 14px 34px rgba(0,0,0,.35); min-width:190px;
}}
.theme-panel.open {{ display:block; }}
.theme-panel .label {{ font-size:.7rem; color:var(--text-secondary); margin-bottom:10px; text-transform:uppercase; letter-spacing:.06em; }}
.theme-swatch-row {{ display:flex; flex-wrap:wrap; gap:10px; }}
.theme-swatch {{ width:30px; height:30px; border-radius:50%; cursor:pointer; border:2px solid transparent; box-shadow:0 0 0 1px rgba(255,255,255,.08); }}
.theme-swatch.active {{ border-color:#6366f1; }}
"""

THEME_BODY = """
<button class="theme-fab" id="themeFab" title="Change theme">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4 12H2m20 0h-2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/></svg>
</button>
<div class="theme-panel" id="themePanel">
    <div class="label">Theme</div>
    <div class="theme-swatch-row">
        <div class="theme-swatch" data-theme="midnight" style="background:#111827;" title="Midnight"></div>
        <div class="theme-swatch" data-theme="daylight" style="background:#eef1f6;" title="Daylight"></div>
        <div class="theme-swatch" data-theme="cream" style="background:#faf6ef;" title="Cream"></div>
        <div class="theme-swatch" data-theme="ocean" style="background:#eaf4fb;" title="Ocean"></div>
        <div class="theme-swatch" data-theme="mint" style="background:#eafaf3;" title="Mint"></div>
        <div class="theme-swatch" data-theme="rose" style="background:#fdf1f5;" title="Rose"></div>
    </div>
</div>
"""

THEME_SCRIPT = """
(function () {{
    const themeFab = document.getElementById('themeFab');
    const themePanel = document.getElementById('themePanel');
    themeFab.onclick = (e) => {{ e.stopPropagation(); themePanel.classList.toggle('open'); }};
    document.addEventListener('click', () => themePanel.classList.remove('open'));
    document.querySelectorAll('.theme-swatch').forEach(sw => {{
        sw.onclick = (e) => {{
            e.stopPropagation();
            const theme = sw.dataset.theme;
            if (theme === 'midnight') document.body.removeAttribute('data-theme');
            else document.body.setAttribute('data-theme', theme);
            localStorage.setItem('f2l_theme', theme);
            document.querySelectorAll('.theme-swatch').forEach(s => s.classList.remove('active'));
            sw.classList.add('active');
        }};
    }});
    const saved = localStorage.getItem('f2l_theme') || 'midnight';
    if (saved !== 'midnight') document.body.setAttribute('data-theme', saved);
    const activeSwatch = document.querySelector('.theme-swatch[data-theme="' + saved + '"]');
    if (activeSwatch) activeSwatch.classList.add('active');
}})();
"""

# =========================================================================
# HTML TEMPLATES
# =========================================================================

VIDEO_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{display_title} - File 2 Links</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
""" + THEME_STYLE + """
body {{
    background: var(--bg-page); color:var(--text-primary);
    font-family:'Segoe UI',Roboto,Tahoma,Geneva,Verdana,sans-serif;
    min-height:100vh; padding:16px; display:flex; flex-direction:column; align-items:center;
}}
.page {{ width:100%; max-width:1100px; display:flex; flex-direction:column; gap:20px; }}

/* ---------- PLAYER ---------- */
.player-shell {{
    background:var(--bg-panel); border:1px solid var(--border-color); border-radius:18px;
    overflow:hidden; box-shadow:0 25px 60px -12px rgba(0,0,0,0.6);
}}
.player-box {{
    position:relative; width:100%; background:#000; aspect-ratio:16/9;
    display:flex; align-items:center; justify-content:center; user-select:none;
}}
.player-box video {{ width:100%; height:100%; object-fit:contain; display:block; filter:brightness(1); }}

.audio-cover {{
    position:absolute; inset:0; z-index:2; display:{audio_cover_display}; align-items:center; justify-content:center;
    background:linear-gradient(135deg,#1e293b,#0f172a); pointer-events:none;
}}
.audio-cover svg {{ width:64px; height:64px; opacity:.85; }}

.loader {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; background:rgba(0,0,0,0.4); z-index:6; transition:opacity .25s ease; }}
.loader.hidden {{ opacity:0; pointer-events:none; }}
.spinner {{ width:46px; height:46px; border:4px solid rgba(255,255,255,.15); border-top-color:#6366f1; border-radius:50%; animation:spin .8s linear infinite; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}

.center-play {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:5; cursor:pointer; }}
.center-play .play-circle {{
    width:78px; height:78px; border-radius:50%; background:rgba(17,24,39,0.72);
    border:2px solid rgba(255,255,255,0.25); display:flex; align-items:center; justify-content:center;
    box-shadow:0 10px 28px rgba(0,0,0,.55); transition:transform .15s ease, background .15s ease;
}}
.center-play:hover .play-circle {{ background:rgba(99,102,241,0.85); transform:scale(1.06); }}
.center-play svg {{ width:32px; height:32px; margin-left:3px; }}
.center-play.hidden {{ display:none; }}

.top-bar {{
    position:absolute; top:0; left:0; right:0; padding:12px 16px; z-index:4;
    background:linear-gradient(to bottom, rgba(0,0,0,.6), transparent);
    display:flex; align-items:center; pointer-events:none;
}}
.top-bar a {{ pointer-events:auto; color:#fff; text-decoration:none; font-weight:700; font-size:.92rem; text-shadow:0 2px 6px rgba(0,0,0,.7); white-space:nowrap; }}
.controls-hidden .top-bar {{ opacity:0; transition:opacity .3s ease; }}

.controls {{ position:absolute; left:0; right:0; bottom:0; z-index:4; padding:10px 14px 14px; background:linear-gradient(to top, rgba(0,0,0,.85), transparent); opacity:1; transition:opacity .3s ease; }}
.controls-hidden .controls {{ opacity:0; pointer-events:none; }}

.seek-row {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
.seek-bar {{ flex:1; position:relative; height:14px; display:flex; align-items:center; cursor:pointer; }}
.seek-track {{ position:absolute; left:0; right:0; height:4px; border-radius:4px; background:rgba(255,255,255,.25); }}
.seek-buffer {{ position:absolute; left:0; height:4px; border-radius:4px; background:rgba(255,255,255,.4); width:0%; }}
.seek-fill {{ position:absolute; left:0; height:4px; border-radius:4px; background:#6366f1; width:0%; }}
.seek-thumb {{ position:absolute; width:13px; height:13px; border-radius:50%; background:#6366f1; left:0%; transform:translateX(-50%); box-shadow:0 0 0 4px rgba(99,102,241,.25); }}
.time-label {{ font-size:.78rem; color:#cbd5e1; min-width:44px; text-align:center; }}

.controls-row {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
.ctrl-left, .ctrl-right {{ display:flex; align-items:center; gap:12px; }}
.ctrl-right {{ margin-left:auto; }}
.icon-btn {{ background:none; border:none; color:#f8fafc; cursor:pointer; padding:6px; display:flex; align-items:center; justify-content:center; border-radius:8px; transition:background .15s ease; }}
.icon-btn:hover {{ background:rgba(255,255,255,.12); }}
.icon-btn svg {{ width:22px; height:22px; }}

.volume-wrap {{ display:flex; align-items:center; gap:6px; }}
input[type=range] {{ -webkit-appearance:none; appearance:none; width:70px; height:4px; border-radius:4px; background:rgba(255,255,255,.25); outline:none; cursor:pointer; }}
input[type=range]::-webkit-slider-thumb {{ -webkit-appearance:none; width:12px; height:12px; border-radius:50%; background:#6366f1; cursor:pointer; }}
input[type=range]::-moz-range-thumb {{ width:12px; height:12px; border-radius:50%; background:#6366f1; border:none; cursor:pointer; }}

.menu-wrap {{ position:relative; }}
.dropdown {{ position:absolute; bottom:38px; right:0; background:#1c2333; border:1px solid rgba(255,255,255,.1); border-radius:10px; padding:6px; display:none; min-width:190px; box-shadow:0 10px 30px rgba(0,0,0,.5); z-index:10; }}
.dropdown.dropdown-below {{ bottom:auto; top:44px; }}
.dropdown.open {{ display:block; }}
.dropdown .row {{ padding:8px 10px; font-size:.85rem; border-radius:6px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; color:#f1f5f9; text-decoration:none; }}
.dropdown .row:hover {{ background:rgba(255,255,255,.08); }}
.dropdown .row.active {{ color:#a5b4fc; font-weight:700; }}
.dropdown .sub-label {{ font-size:.72rem; color:#94a3b8; padding:6px 10px 2px; }}
.dropdown .caveat {{ font-size:.68rem; color:#64748b; padding:6px 10px 2px; line-height:1.35; }}
.brightness-slider-wrap {{ display:flex; align-items:center; gap:8px; padding:8px 10px; }}

/* ---------- INFO PANEL ---------- */
.info-panel {{ padding:20px 22px; display:flex; flex-direction:column; gap:16px; }}
.title {{
    font-size:1.15rem; font-weight:700; color:var(--text-primary); word-break:break-word;
    overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical;
}}
.meta {{ display:flex; gap:8px; flex-wrap:wrap; }}
.badge {{ background:var(--badge-bg); color:var(--badge-text); padding:3px 10px; border-radius:20px; font-size:.78rem; font-weight:600; }}

/* ---------- CHANNEL ROW (YouTube-style uploader chip) ---------- */
.channel-row {{ display:flex; align-items:center; gap:12px; }}
.avatar-wrap {{ position:relative; width:44px; height:44px; border-radius:50%; overflow:hidden; flex-shrink:0; background:#374151; }}
.avatar-img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.avatar-fallback {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#fff; font-weight:700; font-size:1.1rem; background:linear-gradient(135deg,#6366f1,#8b5cf6); }}
.channel-name {{ font-weight:600; color:var(--text-primary); font-size:.92rem; }}
.channel-sub {{ font-size:.72rem; color:var(--text-secondary); }}

/* ---------- YOUTUBE-STYLE ACTION ROW ---------- */
.yt-actions {{ display:flex; gap:10px; overflow-x:auto; padding-bottom:2px; }}
.yt-action {{
    display:flex; align-items:center; gap:8px; padding:10px 16px; border-radius:20px;
    background:var(--badge-bg); color:var(--text-primary); border:none; cursor:pointer;
    font-size:.85rem; font-weight:600; white-space:nowrap; text-decoration:none; flex-shrink:0;
    transition:filter .15s ease;
}}
.yt-action:hover {{ filter:brightness(1.15); }}
.yt-action svg {{ width:19px; height:19px; }}

.footer-note {{ font-size:.76rem; color:var(--text-secondary); text-align:center; }}

/* ---------- RECOMMENDATIONS ---------- */
.rec-section h3 {{ font-size:1rem; margin-bottom:14px; color:var(--text-primary); }}
.rec-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; }}
.rec-card {{ background:var(--bg-card); border:1px solid var(--border-color); border-radius:14px; overflow:hidden; cursor:pointer; transition:transform .18s ease, box-shadow .18s ease; text-decoration:none; color:inherit; }}
.rec-card:hover {{ transform:translateY(-3px); box-shadow:0 14px 30px rgba(0,0,0,.35); }}
.rec-thumb {{ position:relative; width:100%; aspect-ratio:16/9; background:#1c2333; overflow:hidden; }}
.rec-thumb img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.rec-thumb-fallback {{ position:absolute; inset:0; display:none; align-items:center; justify-content:center; }}
.rec-thumb-fallback svg {{ width:34px; height:34px; opacity:.4; }}
.rec-info {{ padding:10px 12px; }}
.rec-title {{ font-size:.86rem; font-weight:600; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.rec-meta {{ font-size:.72rem; color:var(--text-secondary); margin-top:4px; }}
.rec-empty {{ color:var(--text-secondary); font-size:.85rem; }}

@media screen and (max-width: 900px) and (orientation: portrait) {{
    .player-box:fullscreen, .player-box:-webkit-full-screen {{
        transform: rotate(90deg);
        transform-origin: center center;
        width: 100vh;
        height: 100vw;
        position: fixed;
        top: 50%; left: 50%;
        margin-top: -50vw; margin-left: -50vh;
    }}
}}
</style>
</head>
<body>
""" + THEME_BODY + """
<div class="page">
    <div class="player-shell">
        <div class="player-box" id="playerBox">
            <div class="audio-cover" id="audioCover">
                <svg viewBox="0 0 24 24" fill="white"><path d="M12 3v10.55A4 4 0 1014 17V7h4V3h-6z"/></svg>
            </div>
            <div class="loader" id="loader"><div class="spinner"></div></div>
            <div class="center-play" id="centerPlay">
                <div class="play-circle"><svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg></div>
            </div>
            <div class="top-bar" id="topBar"><a href="{bot_link}">🎬 {bot_name}</a></div>
            <video id="video" playsinline preload="metadata">
                <source src="{stream_url}" type="{mime_type}">
            </video>

            <div class="controls" id="controls">
                <div class="seek-row">
                    <span class="time-label" id="curTime">0:00</span>
                    <div class="seek-bar" id="seekBar">
                        <div class="seek-track"></div>
                        <div class="seek-buffer" id="seekBuffer"></div>
                        <div class="seek-fill" id="seekFill"></div>
                        <div class="seek-thumb" id="seekThumb"></div>
                    </div>
                    <span class="time-label" id="durTime">0:00</span>
                </div>
                <div class="controls-row">
                    <div class="ctrl-left">
                        <button class="icon-btn" id="playBtn" title="Play/Pause">
                            <svg viewBox="0 0 24 24" fill="white"><path id="playIcon" d="M8 5v14l11-7z"/></svg>
                        </button>
                        <button class="icon-btn" id="backBtn" title="-30s">
                            <svg viewBox="0 0 24 24" fill="white"><path d="M12 5V1L7 6l5 5V7c3.3 0 6 2.7 6 6s-2.7 6-6 6-6-2.7-6-6H4c0 4.4 3.6 8 8 8s8-3.6 8-8-3.6-8-8-8z"/></svg>
                        </button>
                        <button class="icon-btn" id="fwdBtn" title="+30s">
                            <svg viewBox="0 0 24 24" fill="white"><path d="M12 5V1l5 5-5 5V7c-3.3 0-6 2.7-6 6s2.7 6 6 6 6-2.7 6-6h2c0 4.4-3.6 8-8 8s-8-3.6-8-8 3.6-8 8-8z"/></svg>
                        </button>
                        <div class="volume-wrap">
                            <button class="icon-btn" id="muteBtn" title="Mute">
                                <svg viewBox="0 0 24 24" fill="white" id="volIcon"><path d="M3 10v4h4l5 5V5L7 10H3z"/></svg>
                            </button>
                            <input type="range" id="volumeSlider" min="0" max="100" value="100">
                        </div>
                    </div>
                    <div class="ctrl-right">
                        <div class="menu-wrap" id="audioTrackWrap" style="display:none;">
                            <button class="icon-btn" id="audioBtn" title="Audio track">
                                <svg viewBox="0 0 24 24" fill="white"><path d="M12 3a1 1 0 011 1v6.17a3 3 0 11-2-2.83V4a1 1 0 011-1zM4 10a1 1 0 011 1v3a1 1 0 01-2 0v-3a1 1 0 011-1zm16 0a1 1 0 011 1v3a1 1 0 01-2 0v-3a1 1 0 011-1z"/></svg>
                            </button>
                            <div class="dropdown" id="audioMenu"></div>
                        </div>
                        <div class="menu-wrap">
                            <button class="icon-btn" id="brightnessBtn" title="Brightness">
                                <svg viewBox="0 0 24 24" fill="white"><path d="M12 7a5 5 0 100 10 5 5 0 000-10zm0-5h0v3h0V2zm0 17h0v3h0v-3zM4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h3M19 12h3M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
                            </button>
                            <div class="dropdown" id="brightnessMenu">
                                <div class="sub-label">Brightness</div>
                                <div class="brightness-slider-wrap">
                                    <input type="range" id="brightnessSlider" min="50" max="150" value="100" style="width:150px;">
                                </div>
                            </div>
                        </div>
                        <div class="menu-wrap">
                            <button class="icon-btn" id="speedBtn" title="Playback speed">
                                <svg viewBox="0 0 24 24" fill="white"><path d="M12 20a8 8 0 100-16 8 8 0 000 16zm.5-13v5.2l4 2.4-.7 1.2L11 13V7h1.5z"/></svg>
                            </button>
                            <div class="dropdown" id="speedMenu">
                                <div class="sub-label">Playback speed</div>
                                <div class="row" data-speed="0.5">0.5x</div>
                                <div class="row" data-speed="0.75">0.75x</div>
                                <div class="row active" data-speed="1">Normal</div>
                                <div class="row" data-speed="1.25">1.25x</div>
                                <div class="row" data-speed="1.5">1.5x</div>
                                <div class="row" data-speed="2">2x</div>
                            </div>
                        </div>
                        <button class="icon-btn" id="fullscreenBtn" title="Fullscreen">
                            <svg viewBox="0 0 24 24" fill="white"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <div class="info-panel">
            <div class="title">{display_title}</div>

            <div class="channel-row">
                <div class="avatar-wrap">
                    <img class="avatar-img" src="/avatar?uid={uploader_id}" alt=""
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <div class="avatar-fallback" style="display:none;">{uploader_initial}</div>
                </div>
                <div>
                    <div class="channel-name">{uploader_name}</div>
                    <div class="channel-sub">Uploader</div>
                </div>
            </div>

            <div class="yt-actions">
                <a href="{download_url}" class="yt-action" id="downloadAction">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.6l3.3-3.3 1.4 1.4L12 17 6.3 11.7l1.4-1.4L11 13.6V3h1zM5 19h14v2H5z"/></svg>
                    Download
                </a>
                <button class="yt-action" id="shareAction">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.1c-.8 0-1.5.3-2 .8l-7.1-4.1c.1-.3.1-.5.1-.8s0-.5-.1-.8L15.9 7c.5.5 1.2.8 2.1.8 1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3c0 .3 0 .5.1.8L7.9 9.7C7.4 9.3 6.7 9 6 9c-1.7 0-3 1.3-3 3s1.3 3 3 3c.7 0 1.4-.3 1.9-.7l7.2 4.1c-.1.2-.1.5-.1.7 0 1.6 1.3 3 3 3s3-1.4 3-3-1.3-3-3-3z"/></svg>
                    Share
                </button>
                <div class="menu-wrap">
                    <button class="yt-action" id="openWithAction">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 3v2h3.6l-9.8 9.8 1.4 1.4L19 6.4V10h2V3h-7zM5 5h5V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-5h-2v5H5V5z"/></svg>
                        Open with
                    </button>
                    <div class="dropdown dropdown-below" id="openWithMenu">
                        <div class="sub-label">Open in external player</div>
                        <a class="row" href="{mx_url}">🟧 MX Player</a>
                        <a class="row" href="{vlc_url}">🔴 VLC Player</a>
                    </div>
                </div>
            </div>

            <div class="meta">
                <span class="badge">💾 {file_size}</span>
                <span class="badge">⚡ Seekable Stream</span>
            </div>
            <div class="footer-note">Powered by File 2 Links — streamed directly from Telegram</div>
        </div>
    </div>

    <div class="rec-section">
        <h3>▶ More videos</h3>
        <div class="rec-grid" id="recGrid">
            <div class="rec-empty">Loading recommendations…</div>
        </div>
    </div>
</div>

<script>
const CURRENT_ID = {msg_id};
const STREAM_URL = "{stream_url}";
const SHARE_TITLE = "{display_title_js}";
const video = document.getElementById('video');
const playerBox = document.getElementById('playerBox');
const loader = document.getElementById('loader');
const centerPlay = document.getElementById('centerPlay');
const playBtn = document.getElementById('playBtn');
const playIcon = document.getElementById('playIcon');
const backBtn = document.getElementById('backBtn');
const fwdBtn = document.getElementById('fwdBtn');
const muteBtn = document.getElementById('muteBtn');
const volIcon = document.getElementById('volIcon');
const volumeSlider = document.getElementById('volumeSlider');
const brightnessBtn = document.getElementById('brightnessBtn');
const brightnessMenu = document.getElementById('brightnessMenu');
const brightnessSlider = document.getElementById('brightnessSlider');
const speedBtn = document.getElementById('speedBtn');
const speedMenu = document.getElementById('speedMenu');
const audioTrackWrap = document.getElementById('audioTrackWrap');
const audioBtn = document.getElementById('audioBtn');
const audioMenu = document.getElementById('audioMenu');
const fullscreenBtn = document.getElementById('fullscreenBtn');
const seekBar = document.getElementById('seekBar');
const seekFill = document.getElementById('seekFill');
const seekBuffer = document.getElementById('seekBuffer');
const seekThumb = document.getElementById('seekThumb');
const curTime = document.getElementById('curTime');
const durTime = document.getElementById('durTime');
const shareAction = document.getElementById('shareAction');
const openWithAction = document.getElementById('openWithAction');
const openWithMenu = document.getElementById('openWithMenu');

const PLAY_PATH = 'M8 5v14l11-7z';
const PAUSE_PATH = 'M6 5h4v14H6zm8 0h4v14h-4z';

function fmtTime(s) {{
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60).toString().padStart(2, '0');
    return `${{m}}:${{sec}}`;
}}

function togglePlay() {{ if (video.paused) video.play(); else video.pause(); }}
playBtn.onclick = togglePlay;
centerPlay.onclick = togglePlay;
playerBox.addEventListener('dblclick', () => toggleFullscreen());

video.addEventListener('play', () => {{ playIcon.setAttribute('d', PAUSE_PATH); centerPlay.classList.add('hidden'); }});
video.addEventListener('pause', () => {{ playIcon.setAttribute('d', PLAY_PATH); centerPlay.classList.remove('hidden'); }});
video.addEventListener('waiting', () => loader.classList.remove('hidden'));
video.addEventListener('canplay', () => loader.classList.add('hidden'));
video.addEventListener('playing', () => loader.classList.add('hidden'));
video.addEventListener('loadedmetadata', () => {{ durTime.textContent = fmtTime(video.duration); populateAudioTracks(); }});

video.addEventListener('timeupdate', () => {{
    if (video.duration) {{
        const pct = (video.currentTime / video.duration) * 100;
        seekFill.style.width = pct + '%';
        seekThumb.style.left = pct + '%';
    }}
    curTime.textContent = fmtTime(video.currentTime);
}});

video.addEventListener('progress', () => {{
    if (video.buffered.length && video.duration) {{
        const end = video.buffered.end(video.buffered.length - 1);
        seekBuffer.style.width = (end / video.duration * 100) + '%';
    }}
}});

backBtn.onclick = () => video.currentTime = Math.max(0, video.currentTime - 30);
fwdBtn.onclick = () => video.currentTime = Math.min(video.duration || 0, video.currentTime + 30);

function seekTo(clientX) {{
    const rect = seekBar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    if (video.duration) video.currentTime = ratio * video.duration;
}}
let seeking = false;
seekBar.addEventListener('mousedown', e => {{ seeking = true; seekTo(e.clientX); }});
window.addEventListener('mousemove', e => {{ if (seeking) seekTo(e.clientX); }});
window.addEventListener('mouseup', () => seeking = false);
seekBar.addEventListener('touchstart', e => {{ seeking = true; seekTo(e.touches[0].clientX); }});
seekBar.addEventListener('touchmove', e => {{ if (seeking) seekTo(e.touches[0].clientX); }});
seekBar.addEventListener('touchend', () => seeking = false);

muteBtn.onclick = () => {{
    video.muted = !video.muted;
    volIcon.style.opacity = video.muted ? '0.4' : '1';
    volumeSlider.value = video.muted ? 0 : (video.volume * 100);
}};
volumeSlider.addEventListener('input', () => {{
    video.volume = volumeSlider.value / 100;
    video.muted = video.volume === 0;
    volIcon.style.opacity = video.muted ? '0.4' : '1';
}});

function closeAllMenus(except) {{
    [brightnessMenu, speedMenu, audioMenu, openWithMenu].forEach(m => {{ if (m !== except) m.classList.remove('open'); }});
}}
brightnessBtn.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(brightnessMenu); brightnessMenu.classList.toggle('open'); }};
speedBtn.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(speedMenu); speedMenu.classList.toggle('open'); }};
audioBtn.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(audioMenu); audioMenu.classList.toggle('open'); }};
openWithAction.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(openWithMenu); openWithMenu.classList.toggle('open'); }};
document.addEventListener('click', () => closeAllMenus(null));

brightnessSlider.addEventListener('input', () => {{ video.style.filter = `brightness(${{brightnessSlider.value / 100}})`; }});

speedMenu.querySelectorAll('.row').forEach(row => {{
    row.addEventListener('click', (e) => {{
        e.stopPropagation();
        video.playbackRate = parseFloat(row.dataset.speed);
        speedMenu.querySelectorAll('.row').forEach(r => r.classList.remove('active'));
        row.classList.add('active');
        speedMenu.classList.remove('open');
    }});
}});

// ---------- Share (YouTube-style) ----------
shareAction.onclick = async () => {{
    if (navigator.share) {{
        try {{ await navigator.share({{ title: SHARE_TITLE, url: STREAM_URL }}); return; }} catch (e) {{ /* user cancelled or unsupported, fall through */ }}
    }}
    try {{
        await navigator.clipboard.writeText(STREAM_URL);
        const original = shareAction.innerHTML;
        shareAction.innerHTML = '✅ Link copied';
        setTimeout(() => shareAction.innerHTML = original, 1500);
    }} catch (e) {{}}
}};

// ---------- Audio track switcher ----------
// Browsers can only enumerate/switch tracks that are natively decodable
// (e.g. AAC). Dolby Digital / Dolby Digital Plus (AC3 / E-AC3) tracks have
// no royalty-free decoder in any mainstream browser, so even if a track is
// listed, audio for it won't play — that's a browser/codec limitation, not
// something this page can work around without server-side transcoding.
function populateAudioTracks() {{
    if (!('audioTracks' in video) || !video.audioTracks || video.audioTracks.length <= 1) {{
        audioTrackWrap.style.display = 'none';
        return;
    }}
    audioTrackWrap.style.display = 'flex';
    audioMenu.innerHTML = '<div class="sub-label">Audio Track</div>';
    for (let i = 0; i < video.audioTracks.length; i++) {{
        const track = video.audioTracks[i];
        const row = document.createElement('div');
        row.className = 'row' + (track.enabled ? ' active' : '');
        row.textContent = track.label || track.language || ('Track ' + (i + 1));
        row.onclick = (e) => {{
            e.stopPropagation();
            for (let j = 0; j < video.audioTracks.length; j++) video.audioTracks[j].enabled = (j === i);
            audioMenu.querySelectorAll('.row').forEach(r => r.classList.remove('active'));
            row.classList.add('active');
            audioMenu.classList.remove('open');
        }};
        audioMenu.appendChild(row);
    }}
    const offRow = document.createElement('div');
    offRow.className = 'row';
    offRow.textContent = 'OFF (mute)';
    offRow.onclick = (e) => {{ e.stopPropagation(); video.muted = true; audioMenu.classList.remove('open'); }};
    audioMenu.appendChild(offRow);
    const caveat = document.createElement('div');
    caveat.className = 'caveat';
    caveat.textContent = 'Only browser-decodable tracks (e.g. AAC) play. Dolby/EAC3 tracks are not supported natively by any browser.';
    audioMenu.appendChild(caveat);
}}

// ---------- Fullscreen — force landscape ----------
async function toggleFullscreen() {{
    if (!document.fullscreenElement) {{
        try {{ await playerBox.requestFullscreen(); }} catch (e) {{}}
        if (screen.orientation && screen.orientation.lock) {{
            try {{ await screen.orientation.lock('landscape'); }} catch (e) {{}}
        }}
    }} else {{
        if (screen.orientation && screen.orientation.unlock) {{
            try {{ screen.orientation.unlock(); }} catch (e) {{}}
        }}
        try {{ await document.exitFullscreen(); }} catch (e) {{}}
    }}
}}
fullscreenBtn.onclick = toggleFullscreen;

let hideTimer;
function showControls() {{
    playerBox.classList.remove('controls-hidden');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {{ if (!video.paused) playerBox.classList.add('controls-hidden'); }}, 3000);
}}
playerBox.addEventListener('mousemove', showControls);
playerBox.addEventListener('touchstart', showControls);
showControls();

// ---------- Recommendations (YouTube-style "up next") ----------
fetch(`/api/suggestions?id=${{CURRENT_ID}}`)
    .then(r => r.json())
    .then(items => {{
        const grid = document.getElementById('recGrid');
        if (!items.length) {{ grid.innerHTML = '<div class="rec-empty">No other videos yet.</div>'; return; }}
        grid.innerHTML = items.map(item => `
            <a class="rec-card" href="/watch?id=${{item.msg_id}}">
                <div class="rec-thumb">
                    <img src="/thumb?id=${{item.msg_id}}" loading="lazy" alt=""
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <div class="rec-thumb-fallback">
                        <svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>
                    </div>
                </div>
                <div class="rec-info">
                    <div class="rec-title">${{item.display_title}}</div>
                    <div class="rec-meta">💾 ${{item.file_size_str}}</div>
                </div>
            </a>
        `).join('');
    }})
    .catch(() => {{ document.getElementById('recGrid').innerHTML = '<div class="rec-empty">Could not load recommendations.</div>'; }});
</script>
<script>""" + THEME_SCRIPT + """</script>
</body>
</html>
"""

LIBRARY_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your Library - File 2 Links</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
""" + THEME_STYLE + """
body {{
    background:var(--bg-page); color:var(--text-primary);
    font-family:'Segoe UI',Roboto,Tahoma,Geneva,Verdana,sans-serif;
    min-height:100vh; padding:20px 16px 40px;
}}
.wrap {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:1.4rem; margin-bottom:4px; }}
.sub {{ color:var(--text-secondary); font-size:.9rem; margin-bottom:22px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:16px; }}
.card {{ background:var(--bg-card); border:1px solid var(--border-color); border-radius:14px; overflow:hidden; cursor:pointer; transition:transform .18s ease, box-shadow .18s ease; text-decoration:none; color:inherit; }}
.card:hover {{ transform:translateY(-3px); box-shadow:0 14px 30px rgba(0,0,0,.35); }}
.thumb {{ position:relative; width:100%; aspect-ratio:16/9; background:#1c2333; overflow:hidden; }}
.thumb img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.thumb-fallback {{ position:absolute; inset:0; display:none; align-items:center; justify-content:center; }}
.thumb-fallback svg {{ width:36px; height:36px; opacity:.4; }}
.info {{ padding:10px 12px; }}
.title {{ font-size:.88rem; font-weight:600; color:var(--text-primary); overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.meta {{ font-size:.74rem; color:var(--text-secondary); margin-top:4px; }}
.empty {{ color:var(--text-secondary); font-size:.9rem; margin-top:20px; }}
</style>
</head>
<body>
""" + THEME_BODY + """
<div class="wrap">
    <h1>📚 Your Library</h1>
    <div class="sub">Every video and audio file you've sent, ready to stream.</div>
    <div class="grid" id="grid">
        <div class="empty">Loading your library…</div>
    </div>
</div>
<script>
const params = new URLSearchParams(location.search);
const uid = params.get('uid') || '';
fetch(`/api/library?uid=${{uid}}`)
    .then(r => r.json())
    .then(items => {{
        const grid = document.getElementById('grid');
        if (!items.length) {{ grid.innerHTML = '<div class="empty">No videos yet — send a file to the bot to get started.</div>'; return; }}
        grid.innerHTML = items.map(item => `
            <a class="card" href="/watch?id=${{item.msg_id}}">
                <div class="thumb">
                    <img src="/thumb?id=${{item.msg_id}}" loading="lazy" alt=""
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <div class="thumb-fallback">
                        <svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>
                    </div>
                </div>
                <div class="info">
                    <div class="title">${{item.display_title}}</div>
                    <div class="meta">💾 ${{item.file_size_str}}</div>
                </div>
            </a>
        `).join('');
    }})
    .catch(() => {{ document.getElementById('grid').innerHTML = '<div class="empty">Could not load your library.</div>'; }});
</script>
<script>""" + THEME_SCRIPT + """</script>
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
            text="<b>Access Restricted</b>\n\nPlease join our update channel to continue using <b>File 2 Links</b>.",
            reply_markup=fsub_markup
        )
        return

    bot_username = context.bot.username
    user_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    bot_link = f'<a href="https://t.me/{bot_username}">File 2 Links</a>' if bot_username else "<b>File 2 Links</b>"

    welcome_text = (
        f"<b>{bot_link}</b>\n"
        f"<i>Instant Telegram Media Streaming</i>\n\n"
        f"Welcome, {user_link}. This bot converts any file you send into a secure, "
        f"shareable link — with built-in streaming, MX Player, and VLC support for video and audio.\n\n"
        f"<blockquote>▸ Send a video, audio, photo, or document to begin\n"
        f"▸ Receive an instant stream link and a download link\n"
        f"▸ Play videos directly in MX Player or VLC, one tap away</blockquote>\n\n"
        f"🚫 <b>Please do not share NSFW or illegal content.</b> Violators may be banned.\n\n"
        f"Use the buttons below to get started."
    )
    buttons = markup([
        [btn("✨ Open Web App", web_app_url=f"{BASE_URL}/library?uid={user.id}", style="primary")],
        [btn("📖 Help Guide", callback_data="help_menu", style="success")]
    ])

    await send_start_media(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=buttons,
        media_url=START_PIC
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    has_media = bool(
        query.message.photo or query.message.video or query.message.animation
        or query.message.audio or query.message.document
    )

    if query.data == "help_menu":
        await answer_callback_query(query.id)
        help_text = (
            "<b>Help Center</b>\n\n"
            "<b>How it works</b>\n"
            "<blockquote>1. Send any video, audio, photo, or document\n"
            "2. Receive a Stream Link and a Download Link instantly\n"
            "3. Video & audio also unlock the Web Player, MX Player, and VLC</blockquote>\n\n"
            "<b>Good to know</b>\n"
            "<blockquote>▸ Images open directly in your browser — view and download, no extra steps\n"
            "▸ Documents are served as direct open / download links\n"
            "▸ Large files may take a few seconds to begin streaming</blockquote>\n\n"
            "🚫 <b>NSFW or illegal content is not allowed on this bot.</b>\n\n"
            f"<i>{CREDIT_LINE}</i>"
        )
        buttons = markup([
            [btn("❌ Close", callback_data="close_menu", style="danger")]
        ])
        await edit_message(chat_id, message_id, help_text, reply_markup=buttons, has_media=has_media)

    elif query.data == "close_menu":
        await answer_callback_query(query.id)
        await delete_message(chat_id, message_id)

    elif query.data == "check_fsub":
        is_subscribed = await check_fsub(context.bot, query.from_user.id)
        if is_subscribed:
            await answer_callback_query(query.id)
            await delete_message(chat_id, message_id)
            await send_message(chat_id, "<b>Verified.</b> You're all set — send a file to begin.")
        else:
            await answer_callback_query(query.id, text="You haven't joined the channel yet.", show_alert=True)

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

    # Document, Video, Audio, or Photo — every format Telegram supports is accepted here.
    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    original_caption = message.caption or ""
    file_size_bytes = getattr(media, "file_size", 0)
    file_size_str = get_readable_file_size(file_size_bytes)

    is_photo = bool(message.photo)

    if is_photo:
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

    mime_type = guess_mime_type(filename, getattr(media, "mime_type", None))

    # MKV and other "exotic" containers usually arrive as a generic Document
    # rather than Telegram's native video type — sniff by mime/extension so
    # they still unlock the Watch/MX Player/VLC buttons instead of being
    # treated as a plain document.
    doc_media_kind = classify_media_kind(filename, mime_type) if message.document else None
    is_video_audio = bool(message.video or message.audio or doc_media_kind)
    is_audio_only = bool(message.audio) or (doc_media_kind == "audio")

    thumb_source = message.video or message.document
    has_thumb = bool(getattr(thumb_source, "thumbnail", None)) if thumb_source else False

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
        "uploader_name": user.full_name,
        "filename": filename,
        "display_title": display_title,
        "file_size_str": file_size_str,
        "is_video_audio": is_video_audio,
        "is_audio_only": is_audio_only,
        "is_photo": is_photo,
        "has_thumb": has_thumb,
        "mime_type": mime_type,
        "created_at": int(time.time())
    }
    await files_col.insert_one(file_doc)

    download_url = f"{BASE_URL}/stream?id={log_msg.message_id}&d=true"
    direct_url = f"{BASE_URL}/stream?id={log_msg.message_id}"

    reply_text = (
        f"⚡ <b>File Ready!</b>\n\n"
        f"📁 <b>File Name:</b> <code>{filename}</code>\n"
        f"💾 <b>File Size:</b> <code>{file_size_str}</code>\n\n"
        f"<blockquote>🔗 <b>Stream Link:</b>\n<code>{direct_url}</code>\n\n"
        f"📥 <b>Download Link:</b>\n<code>{download_url}</code></blockquote>"
    )

    if is_video_audio:
        watch_url = f"{BASE_URL}/watch?id={log_msg.message_id}"
        mx_url = f"{BASE_URL}/mx?id={log_msg.message_id}"
        vlc_url = f"{BASE_URL}/vlc?id={log_msg.message_id}"
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
    elif is_photo:
        # Images open and view directly — no separate watch page needed.
        buttons = markup([
            [
                btn("🖼️ View Image", url=direct_url, style="primary"),
                btn("📥 Download", url=download_url, style="success")
            ]
        ])
    else:
        # Documents — direct open/download links, no watch page.
        buttons = markup([
            [
                btn("📄 Open File", url=direct_url, style="primary"),
                btn("📥 Download", url=download_url, style="success")
            ]
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
    """Web player — video & audio only. Images and documents are served
    directly via /stream (view) and /stream?d=true (download)."""
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(text="Invalid parameters", status=400)

    msg_id = int(msg_id_str)
    file_doc = await files_col.find_one({"msg_id": msg_id})
    if not file_doc:
        return web.Response(text="File content not found or expired.", status=404)

    if not file_doc.get("is_video_audio", False):
        raise web.HTTPFound(f"{BASE_URL}/stream?id={msg_id}")

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"
    mx_url = f"{BASE_URL}/mx?id={msg_id}"
    vlc_url = f"{BASE_URL}/vlc?id={msg_id}"

    display_title = file_doc.get("display_title", "Media File")
    file_size_str = file_doc.get("file_size_str", "Unknown Size")
    mime_type = file_doc.get("mime_type") or "video/mp4"
    is_audio_only = file_doc.get("is_audio_only", False)
    uploader_id = file_doc.get("user_id", 0)
    uploader_name = file_doc.get("uploader_name", "Unknown Uploader")
    uploader_initial = uploader_name[0].upper() if uploader_name else "U"

    bot_username = ptb_app.bot.username if ptb_app.bot else None
    bot_link = f"https://t.me/{bot_username}" if bot_username else "https://t.me/"

    html_content = VIDEO_PLAYER_TEMPLATE.format(
        stream_url=stream_url,
        download_url=download_url,
        mx_url=mx_url,
        vlc_url=vlc_url,
        display_title=display_title,
        display_title_js=display_title.replace('"', '\\"'),
        file_size=file_size_str,
        mime_type=mime_type,
        msg_id=msg_id,
        audio_cover_display="flex" if is_audio_only else "none",
        uploader_id=uploader_id,
        uploader_name=uploader_name,
        uploader_initial=uploader_initial,
        bot_name="File 2 Links",
        bot_link=bot_link
    )
    return web.Response(text=html_content, content_type="text/html")

async def handle_library(request):
    # .format() is called even with no placeholders so the doubled braces
    # in THEME_STYLE/THEME_SCRIPT collapse to single braces — without this
    # the inline JavaScript ships broken (literal "{{" / "}}") and the page
    # never gets past "Loading your library…".
    html_content = LIBRARY_TEMPLATE.format()
    return web.Response(text=html_content, content_type="text/html")

async def handle_library_api(request):
    uid = request.query.get("uid")
    query = {"is_video_audio": True}
    if uid and uid.isdigit():
        query["user_id"] = int(uid)

    items = []
    cursor = files_col.find(query).sort("created_at", -1).limit(60)
    async for doc in cursor:
        items.append({
            "msg_id": doc["msg_id"],
            "display_title": doc.get("display_title", "Untitled"),
            "file_size_str": doc.get("file_size_str", "")
        })
    return web.json_response(items)

async def handle_suggestions(request):
    """YouTube-style 'up next' feed — recent videos/audio, excluding the one being watched."""
    current_id = request.query.get("id")
    query = {"is_video_audio": True}
    if current_id and current_id.isdigit():
        query["msg_id"] = {"$ne": int(current_id)}

    items = []
    cursor = files_col.find(query).sort("created_at", -1).limit(8)
    async for doc in cursor:
        items.append({
            "msg_id": doc["msg_id"],
            "display_title": doc.get("display_title", "Untitled"),
            "file_size_str": doc.get("file_size_str", "")
        })
    return web.json_response(items)

async def handle_thumb(request):
    """Streams the Telegram-generated thumbnail for a video/document, used by recommendation cards."""
    msg_id_str = request.query.get("id")
    if not msg_id_str or not msg_id_str.isdigit():
        return web.Response(status=400)

    msg_id = int(msg_id_str)
    try:
        msg = await tg_client.get_messages(LOG_GROUP, msg_id)
        media = msg.video or msg.document
        thumbs = getattr(media, "thumbs", None) if media else None
        if not thumbs:
            return web.Response(status=404)

        thumb_bytes = await tg_client.download_media(thumbs[-1].file_id, in_memory=True)
        if not thumb_bytes:
            return web.Response(status=404)
        thumb_bytes.seek(0)
        return web.Response(body=thumb_bytes.read(), content_type="image/jpeg")
    except Exception:
        return web.Response(status=404)

async def handle_avatar(request):
    """Streams the uploader's Telegram profile photo for the YouTube-style
    'channel row' beneath the player. Falls back to a 404, which the
    front-end swaps for a generated initial-letter avatar."""
    uid_str = request.query.get("uid")
    if not uid_str or not uid_str.isdigit():
        return web.Response(status=400)

    uid = int(uid_str)
    try:
        photo = None
        async for p in tg_client.get_chat_photos(uid, limit=1):
            photo = p
            break
        if not photo:
            return web.Response(status=404)

        photo_bytes = await tg_client.download_media(photo.file_id, in_memory=True)
        if not photo_bytes:
            return web.Response(status=404)
        photo_bytes.seek(0)
        return web.Response(body=photo_bytes.read(), content_type="image/jpeg")
    except Exception:
        return web.Response(status=404)

async def handle_stream(request):
    """Serves the media binary with proper HTTP Range support for ANY audio
    or video format Telegram provides — the actual Content-Type is detected
    from Telegram's own mime_type (falling back to filename-extension
    guessing), it isn't hardcoded to a single format."""
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
            mime_type = guess_mime_type(filename, getattr(media, "mime_type", None))

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
    app.router.add_get("/library", handle_library)
    app.router.add_get("/api/library", handle_library_api)
    app.router.add_get("/stream", handle_stream)
    app.router.add_get("/mx", handle_mx)
    app.router.add_get("/vlc", handle_vlc)
    app.router.add_get("/api/suggestions", handle_suggestions)
    app.router.add_get("/thumb", handle_thumb)
    app.router.add_get("/avatar", handle_avatar)

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

    print("=" * 60)
    print("🚀 File 2 Links — service is live on port", PORT)
    print("👤 Credits:", CREDIT_LINE)
    print("=" * 60)

    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
