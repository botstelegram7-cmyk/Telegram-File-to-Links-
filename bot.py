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
body {{
    background: radial-gradient(circle at top, #131b2e 0%, #05070d 65%);
    color:#f8fafc; font-family:'Segoe UI',Roboto,Tahoma,Geneva,Verdana,sans-serif;
    min-height:100vh; padding:16px; display:flex; flex-direction:column; align-items:center;
}}
.page {{ width:100%; max-width:1100px; display:flex; flex-direction:column; gap:20px; }}

/* ---------- PLAYER ---------- */
.player-shell {{
    background:#111827; border:1px solid rgba(255,255,255,0.08); border-radius:18px;
    overflow:hidden; box-shadow:0 25px 60px -12px rgba(0,0,0,0.8);
}}
.player-box {{
    position:relative; width:100%; background:#000; aspect-ratio:16/9;
    display:flex; align-items:center; justify-content:center;
    user-select:none;
}}
.player-box video {{
    width:100%; height:100%; object-fit:contain; display:block; filter:brightness(1);
}}
.loader {{
    position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    background:rgba(0,0,0,0.4); z-index:6; transition:opacity .25s ease;
}}
.loader.hidden {{ opacity:0; pointer-events:none; }}
.spinner {{ width:46px; height:46px; border:4px solid rgba(255,255,255,.15); border-top-color:#6366f1; border-radius:50%; animation:spin .8s linear infinite; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}

.center-play {{
    position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
    z-index:5; cursor:pointer; background:rgba(0,0,0,0.25);
}}
.center-play svg {{ width:74px; height:74px; filter:drop-shadow(0 4px 14px rgba(0,0,0,.6)); }}
.center-play.hidden {{ display:none; }}

.top-bar {{
    position:absolute; top:0; left:0; right:0; padding:16px;
    background:linear-gradient(to bottom, rgba(0,0,0,.65), transparent);
    font-weight:600; font-size:1rem; z-index:4; opacity:1; transition:opacity .3s ease;
    text-shadow:0 2px 6px rgba(0,0,0,.7); pointer-events:none;
}}
.controls-hidden .top-bar {{ opacity:0; }}

.controls {{
    position:absolute; left:0; right:0; bottom:0; z-index:4;
    padding:10px 14px 14px; background:linear-gradient(to top, rgba(0,0,0,.85), transparent);
    opacity:1; transition:opacity .3s ease;
}}
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
.icon-btn {{
    background:none; border:none; color:#f8fafc; cursor:pointer; padding:6px;
    display:flex; align-items:center; justify-content:center; border-radius:8px; transition:background .15s ease;
}}
.icon-btn:hover {{ background:rgba(255,255,255,.12); }}
.icon-btn svg {{ width:22px; height:22px; }}

.volume-wrap {{ display:flex; align-items:center; gap:6px; }}
input[type=range] {{
    -webkit-appearance:none; appearance:none; width:70px; height:4px; border-radius:4px;
    background:rgba(255,255,255,.25); outline:none; cursor:pointer;
}}
input[type=range]::-webkit-slider-thumb {{
    -webkit-appearance:none; width:12px; height:12px; border-radius:50%; background:#6366f1; cursor:pointer;
}}
input[type=range]::-moz-range-thumb {{ width:12px; height:12px; border-radius:50%; background:#6366f1; border:none; cursor:pointer; }}

.menu-wrap {{ position:relative; }}
.dropdown {{
    position:absolute; bottom:38px; right:0; background:#1c2333; border:1px solid rgba(255,255,255,.1);
    border-radius:10px; padding:6px; display:none; min-width:170px; box-shadow:0 10px 30px rgba(0,0,0,.5); z-index:10;
}}
.dropdown.open {{ display:block; }}
.dropdown .row {{ padding:8px 10px; font-size:.85rem; border-radius:6px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; }}
.dropdown .row:hover {{ background:rgba(255,255,255,.08); }}
.dropdown .row.active {{ color:#a5b4fc; font-weight:700; }}
.dropdown .sub-label {{ font-size:.72rem; color:#94a3b8; padding:6px 10px 2px; }}
.brightness-slider-wrap {{ display:flex; align-items:center; gap:8px; padding:8px 10px; }}

/* ---------- INFO PANEL ---------- */
.info-panel {{ padding:20px 22px; display:flex; flex-direction:column; gap:16px; }}
.title {{ font-size:1.2rem; font-weight:700; color:#f1f5f9; word-break:break-word; }}
.meta {{ display:flex; gap:8px; flex-wrap:wrap; }}
.badge {{ background:rgba(99,102,241,0.15); color:#a5b4fc; padding:3px 10px; border-radius:20px; font-size:.78rem; font-weight:600; }}
.actions {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }}
.btn {{
    padding:13px 16px; border-radius:12px; font-weight:600; font-size:.9rem; text-decoration:none;
    display:inline-flex; align-items:center; justify-content:center; gap:8px; color:#fff; border:none; cursor:pointer;
    transition:all .2s ease; box-shadow:0 4px 12px rgba(0,0,0,.3);
}}
.btn:hover {{ transform:translateY(-2px); filter:brightness(1.08); }}
.btn-orange {{ background:linear-gradient(135deg,#f97316,#ea580c); }}
.btn-red {{ background:linear-gradient(135deg,#ef4444,#dc2626); }}
.btn-green {{ background:linear-gradient(135deg,#10b981,#059669); }}
.btn-blue {{ background:linear-gradient(135deg,#3b82f6,#2563eb); }}
.footer-note {{ font-size:.76rem; color:#4b5563; text-align:center; }}

/* ---------- RECOMMENDATIONS ---------- */
.rec-section h3 {{ font-size:1rem; margin-bottom:14px; color:#e2e8f0; }}
.rec-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; }}
.rec-card {{
    background:#111827; border:1px solid rgba(255,255,255,.06); border-radius:14px; overflow:hidden;
    cursor:pointer; transition:transform .18s ease, box-shadow .18s ease; text-decoration:none; color:inherit;
}}
.rec-card:hover {{ transform:translateY(-3px); box-shadow:0 14px 30px rgba(0,0,0,.5); }}
.rec-thumb {{ width:100%; aspect-ratio:16/9; background:#1c2333; display:flex; align-items:center; justify-content:center; overflow:hidden; }}
.rec-thumb img {{ width:100%; height:100%; object-fit:cover; }}
.rec-thumb svg {{ width:34px; height:34px; opacity:.4; }}
.rec-info {{ padding:10px 12px; }}
.rec-title {{ font-size:.86rem; font-weight:600; color:#e5e7eb; overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.rec-meta {{ font-size:.72rem; color:#94a3b8; margin-top:4px; }}
.rec-empty {{ color:#4b5563; font-size:.85rem; }}
</style>
</head>
<body>
<div class="page">
    <div class="player-shell">
        <div class="player-box" id="playerBox">
            <div class="loader" id="loader"><div class="spinner"></div></div>
            <div class="center-play" id="centerPlay">
                <svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>
            </div>
            <div class="top-bar" id="topBar">🎬 {display_title}</div>
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
                        <button class="icon-btn" id="backBtn" title="-10s">
                            <svg viewBox="0 0 24 24" fill="white"><path d="M12 5V1L7 6l5 5V7c3.3 0 6 2.7 6 6s-2.7 6-6 6-6-2.7-6-6H4c0 4.4 3.6 8 8 8s8-3.6 8-8-3.6-8-8-8z"/></svg>
                        </button>
                        <button class="icon-btn" id="fwdBtn" title="+10s">
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
                        <div class="menu-wrap">
                            <button class="icon-btn" id="brightnessBtn" title="Brightness">
                                <svg viewBox="0 0 24 24" fill="white"><path d="M12 7a5 5 0 100 10 5 5 0 000-10zm0-5h0v3h0V2zm0 17h0v3h0v-3zM4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h3M19 12h3M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
                            </button>
                            <div class="dropdown" id="brightnessMenu">
                                <div class="sub-label">Brightness</div>
                                <div class="brightness-slider-wrap">
                                    <input type="range" id="brightnessSlider" min="50" max="150" value="100" style="width:130px;">
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

    <div class="rec-section">
        <h3>▶ More videos</h3>
        <div class="rec-grid" id="recGrid">
            <div class="rec-empty">Loading recommendations…</div>
        </div>
    </div>
</div>

<script>
const CURRENT_ID = {msg_id};
const video = document.getElementById('video');
const playerBox = document.getElementById('playerBox');
const loader = document.getElementById('loader');
const centerPlay = document.getElementById('centerPlay');
const controls = document.getElementById('controls');
const topBar = document.getElementById('topBar');
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
const fullscreenBtn = document.getElementById('fullscreenBtn');
const seekBar = document.getElementById('seekBar');
const seekFill = document.getElementById('seekFill');
const seekBuffer = document.getElementById('seekBuffer');
const seekThumb = document.getElementById('seekThumb');
const curTime = document.getElementById('curTime');
const durTime = document.getElementById('durTime');

const PLAY_PATH = 'M8 5v14l11-7z';
const PAUSE_PATH = 'M6 5h4v14H6zm8 0h4v14h-4z';

function fmtTime(s) {{
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60).toString().padStart(2, '0');
    return `${{m}}:${{sec}}`;
}}

function togglePlay() {{
    if (video.paused) video.play(); else video.pause();
}}
playBtn.onclick = togglePlay;
centerPlay.onclick = togglePlay;
playerBox.addEventListener('dblclick', () => toggleFullscreen());

video.addEventListener('play', () => {{ playIcon.setAttribute('d', PAUSE_PATH); centerPlay.classList.add('hidden'); }});
video.addEventListener('pause', () => {{ playIcon.setAttribute('d', PLAY_PATH); centerPlay.classList.remove('hidden'); }});
video.addEventListener('waiting', () => loader.classList.remove('hidden'));
video.addEventListener('canplay', () => loader.classList.add('hidden'));
video.addEventListener('playing', () => loader.classList.add('hidden'));
video.addEventListener('loadedmetadata', () => {{ durTime.textContent = fmtTime(video.duration); }});

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

backBtn.onclick = () => video.currentTime = Math.max(0, video.currentTime - 10);
fwdBtn.onclick = () => video.currentTime = Math.min(video.duration || 0, video.currentTime + 10);

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
    [brightnessMenu, speedMenu].forEach(m => {{ if (m !== except) m.classList.remove('open'); }});
}}
brightnessBtn.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(brightnessMenu); brightnessMenu.classList.toggle('open'); }};
speedBtn.onclick = (e) => {{ e.stopPropagation(); closeAllMenus(speedMenu); speedMenu.classList.toggle('open'); }};
document.addEventListener('click', () => closeAllMenus(null));

brightnessSlider.addEventListener('input', () => {{
    video.style.filter = `brightness(${{brightnessSlider.value / 100}})`;
}});

speedMenu.querySelectorAll('.row').forEach(row => {{
    row.addEventListener('click', (e) => {{
        e.stopPropagation();
        video.playbackRate = parseFloat(row.dataset.speed);
        speedMenu.querySelectorAll('.row').forEach(r => r.classList.remove('active'));
        row.classList.add('active');
        speedMenu.classList.remove('open');
    }});
}});

function toggleFullscreen() {{
    if (!document.fullscreenElement) playerBox.requestFullscreen().catch(() => {{}});
    else document.exitFullscreen().catch(() => {{}});
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

function copyLink() {{
    navigator.clipboard.writeText("{stream_url}").then(() => {{
        const original = document.title;
        document.title = "✅ Link Copied!";
        setTimeout(() => document.title = original, 1500);
    }});
}}

// ---------- Recommendations (YouTube-style "up next") ----------
fetch(`/api/suggestions?id=${{CURRENT_ID}}`)
    .then(r => r.json())
    .then(items => {{
        const grid = document.getElementById('recGrid');
        if (!items.length) {{ grid.innerHTML = '<div class="rec-empty">No other videos yet.</div>'; return; }}
        grid.innerHTML = items.map(item => `
            <a class="rec-card" href="/watch?id=${{item.msg_id}}">
                <div class="rec-thumb">
                    ${{item.has_thumb
                        ? `<img src="/thumb?id=${{item.msg_id}}" loading="lazy" onerror="this.parentElement.innerHTML='<svg viewBox=\\'0 0 24 24\\' fill=\\'white\\'><path d=\\'M8 5v14l11-7z\\'/></svg>'">`
                        : `<svg viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>`}}
                </div>
                <div class="rec-info">
                    <div class="rec-title">${{item.display_title}}</div>
                    <div class="rec-meta">💾 ${{item.file_size_str}}</div>
                </div>
            </a>
        `).join('');
    }})
    .catch(() => {{ document.getElementById('recGrid').innerHTML = '<div class="rec-empty">Couldn\\'t load recommendations.</div>'; }});
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
            text=(
                "<b>Access Restricted</b>\n\n"
                "Please join our update channel to continue using <b>File 2 Links</b>."
            ),
            reply_markup=fsub_markup
        )
        return

    welcome_text = (
        f"<b>File 2 Links</b>\n"
        f"<i>Instant Telegram Media Streaming</i>\n\n"
        f"Welcome, {user.first_name}. This bot converts any file you send into a secure, "
        f"shareable link — with built-in streaming, MX Player, and VLC support for video and audio.\n\n"
        f"<blockquote>▸ Send a video, audio, photo, or document to begin\n"
        f"▸ Receive an instant stream link and a download link\n"
        f"▸ Play videos directly in MX Player or VLC, one tap away</blockquote>\n\n"
        f"Use the buttons below to get started."
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
            "<b>Help Center</b>\n\n"
            "<b>How it works</b>\n"
            "<blockquote>1. Send any video, audio, photo, or document\n"
            "2. Receive a Stream Link and a Download Link instantly\n"
            "3. Video & audio also unlock the Web Player, MX Player, and VLC</blockquote>\n\n"
            "<b>Good to know</b>\n"
            "<blockquote>▸ Images open directly in your browser — view and download, no extra steps\n"
            "▸ Documents are served as direct open / download links\n"
            "▸ Large files may take a few seconds to begin streaming</blockquote>"
        )
        buttons = markup([
            [btn("❌ Close", callback_data="close_menu", style="danger")]
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

    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    original_caption = message.caption or ""
    file_size_bytes = getattr(media, "file_size", 0)
    file_size_str = get_readable_file_size(file_size_bytes)

    is_photo = bool(message.photo)
    is_video_audio = bool(message.video or message.audio)

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

    # Detect an embedded thumbnail (used for "up next" recommendation cards).
    thumb_source = message.video or message.document
    has_thumb = bool(getattr(thumb_source, "thumbnail", None)) if thumb_source else False
    mime_type = getattr(media, "mime_type", None) or ("video/mp4" if message.video else "audio/mpeg" if message.audio else "application/octet-stream")

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
        # No watch page for photos/documents — send the browser straight to the direct link.
        raise web.HTTPFound(f"{BASE_URL}/stream?id={msg_id}")

    stream_url = f"{BASE_URL}/stream?id={msg_id}"
    download_url = f"{stream_url}&d=true"
    mx_url = f"{BASE_URL}/mx?id={msg_id}"
    vlc_url = f"{BASE_URL}/vlc?id={msg_id}"

    display_title = file_doc.get("display_title", "Media File")
    file_size_str = file_doc.get("file_size_str", "Unknown Size")
    mime_type = file_doc.get("mime_type") or "video/mp4"

    html_content = VIDEO_PLAYER_TEMPLATE.format(
        stream_url=stream_url,
        download_url=download_url,
        mx_url=mx_url,
        vlc_url=vlc_url,
        display_title=display_title,
        file_size=file_size_str,
        mime_type=mime_type,
        msg_id=msg_id
    )
    return web.Response(text=html_content, content_type="text/html")

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
            "file_size_str": doc.get("file_size_str", ""),
            "has_thumb": doc.get("has_thumb", False)
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
    app.router.add_get("/api/suggestions", handle_suggestions)
    app.router.add_get("/thumb", handle_thumb)

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
