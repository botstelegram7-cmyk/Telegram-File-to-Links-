<div align="center">

# 🎬 File 2 Links

**Instant Telegram File-to-Link Streaming Bot**

Turn any file sent to your bot into a secure streaming + download link — with a custom web player, MX Player & VLC support, and a YouTube-style library.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-22.8-26A5E4?logo=telegram&logoColor=white)](https://python-telegram-bot.org/)
[![Pyrogram](https://img.shields.io/badge/Pyrogram-2.x-2CA5E0?logo=telegram&logoColor=white)](https://pyrogram.org/)
[![License](https://img.shields.io/badge/License-Custom%20MIT%2BDisclaimer-orange)](./LICENSE)
[![Deploy on Render](https://img.shields.io/badge/Deploy-Render-46E3B7?logo=render&logoColor=white)](https://render.com/deploy)
[![Stars](https://img.shields.io/github/stars/your-username/Telegram-File-to-Links?style=social)](https://github.com/your-username/Telegram-File-to-Links/stargazers)
[![Forks](https://img.shields.io/github/forks/your-username/Telegram-File-to-Links?style=social)](https://github.com/your-username/Telegram-File-to-Links/fork)

<video src="https://files.catbox.moe/t6dedx.mp4" controls autoplay loop muted playsinline width="600">
Your browser doesn't support embedded video — <a href="https://files.catbox.moe/t6dedx.mp4">watch the preview here</a>.
</video>

<sub>If the preview above doesn't autoplay (GitHub sometimes strips that), just hit play. ▶️</sub>

</div>

---

## ✨ Features

- 📤 Send any **video, audio, photo, or document** — get an instant Stream Link *and* Download Link
- 🎥 Custom-built web player: seekable progress bar, volume, brightness, playback speed, ±30s skip, fullscreen with forced landscape, multiple light/dark themes
- 🟧 One-tap **MX Player** and 🔴 **VLC** deep links (works even for MKV/AVI files sent as documents)
- 📚 A personal, YouTube-style **library** web app listing every video you've sent, with thumbnails
- ▶️ "Up next" recommendations under the player, like YouTube
- 🖼️ Images open & download directly in the browser — no extra steps
- ⚡ True HTTP Range support — real seeking, not just a spinner
- 🔒 Optional Force-Subscribe gate before the bot will process files
- 🎨 Coloured inline keyboard buttons (Telegram Bot API `style` field)
- 🛠️ All bot messages sent via raw Telegram Bot API HTTP calls

---

## 🧰 Tech Stack

| Layer | Tech |
|---|---|
| Bot framework | [python-telegram-bot](https://python-telegram-bot.org/) 22.x (webhook mode) |
| Streaming | [Pyrogram](https://pyrogram.org/) (MTProto client, chunked range streaming) |
| Web server | `aiohttp` (serves the player, library, and stream endpoints) |
| Database | MongoDB via `motor` (async driver) |
| Deployment | Docker-ready; runs on any host that can expose one HTTP port |

---

## 🔑 Environment Variables

Copy `.env.example` to `.env` and fill in the values below.

| Variable | Required | Description | Where to get it |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | Your bot's token | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `API_ID` | ✅ | Telegram API ID (for Pyrogram) | [my.telegram.org](https://my.telegram.org) → API Development Tools |
| `API_HASH` | ✅ | Telegram API Hash (for Pyrogram) | Same page as `API_ID` |
| `LOG_GROUP` | ✅ | Chat ID of a private channel/group the bot can post to — files are copied here and streamed from here | Add your bot as admin to a private channel, forward a message from it to [@userinfobot](https://t.me/userinfobot) to get the ID (looks like `-100xxxxxxxxxx`) |
| `MONGO_URI` | ✅ | MongoDB connection string | [MongoDB Atlas](https://www.mongodb.com/atlas) free tier, or your own instance |
| `BASE_URL` | ✅ | Public HTTPS URL your service is reachable at (no trailing slash) | Given to you by your host (Render/Railway/your domain) |
| `ADMIN_ID` | ⬜ | Your numeric Telegram user ID — unlocks `/clear` and `/broadcast` | [@userinfobot](https://t.me/userinfobot) |
| `DB_NAME` | ⬜ | MongoDB database name (default: `TelegramStreamBot`) | Your choice |
| `PORT` | ⬜ | Port the web server binds to (default: `10000`) | Set by most hosts automatically |
| `START_PIC` | ⬜ | Image/GIF/video URL shown on `/start` | Any direct media URL |
| `AUTO_DELETE_TIME` | ⬜ | Reserved for future auto-delete support (seconds) | — |
| `WEBHOOK_URL` | ⬜ | Legacy alias for `BASE_URL` (kept for backward compatibility) | — |

> ⚠️ `BOT_TOKEN`, `API_ID`, `API_HASH`, `LOG_GROUP`, `MONGO_URI`, and `BASE_URL` are **mandatory** — the bot will refuse to start without them (see `config.py`).

---

## 🚀 Deployment Guides

### 1. Render (recommended, free tier available)

1. Fork this repo.
2. On [Render](https://render.com), click **New → Web Service**, connect your fork.
3. Environment: **Docker** (the included `Dockerfile` is used automatically).
4. Add all the environment variables from the table above under **Environment**.
5. Set `BASE_URL` to the URL Render gives you *after* the first deploy (e.g. `https://your-service.onrender.com`), then redeploy.
6. Done — Render builds, runs, and keeps it alive.

### 2. Railway

1. Fork this repo → [Railway](https://railway.app) → **New Project → Deploy from GitHub repo**.
2. Railway auto-detects the `Dockerfile`.
3. Add the environment variables in **Variables**.
4. Under **Settings → Networking**, generate a public domain — use that as `BASE_URL`.
5. Redeploy after setting `BASE_URL`.

### 3. Any Ubuntu/Debian VPS (Docker)

```bash
git clone https://github.com/your-username/Telegram-File-to-Links.git
cd Telegram-File-to-Links
cp .env.example .env
nano .env               # fill in your values

docker build -t file2links .
docker run -d --name file2links \
  --env-file .env \
  -p 10000:10000 \
  --restart unless-stopped \
  file2links
```

Put a reverse proxy (Nginx/Caddy) with a real TLS certificate in front of port `10000`, and use that HTTPS domain as `BASE_URL` — Telegram webhooks and MX Player intents both require HTTPS.

### 4. Any VPS without Docker (systemd)

```bash
git clone https://github.com/your-username/Telegram-File-to-Links.git
cd Telegram-File-to-Links
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Create `/etc/systemd/system/file2links.service`:

```ini
[Unit]
Description=File 2 Links Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/Telegram-File-to-Links
EnvironmentFile=/path/to/Telegram-File-to-Links/.env
ExecStart=/path/to/Telegram-File-to-Links/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now file2links
sudo systemctl status file2links
```

Again, put Nginx/Caddy + TLS in front and point `BASE_URL` at the HTTPS domain.

### 5. Docker Compose (self-hosted, with local MongoDB)

```yaml
version: "3.8"
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "10000:10000"
    depends_on:
      - mongo
  mongo:
    image: mongo:7
    restart: unless-stopped
    volumes:
      - mongo_data:/data/db
volumes:
  mongo_data:
```

Set `MONGO_URI=mongodb://mongo:27017` in your `.env` when using this setup.

---

## 🖥️ Local Development

```bash
git clone https://github.com/your-username/Telegram-File-to-Links.git
cd Telegram-File-to-Links
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values — you'll need a tunnel (ngrok/cloudflared) for BASE_URL locally
python bot.py
```

---

## ⭐ Support This Project

If this bot is useful to you:

- **Give it a Star ⭐** — helps other people find it
- **Fork it 🍴** and make it your own
- **Contribute 🤝** — PRs, bug reports, and feature ideas are all welcome. Open an issue first for anything big so we can discuss the approach.

---

## 🚫 Usage Policy

This bot is a general-purpose file streaming/linking tool. **NSFW, illegal, or copyrighted content that you don't have rights to share is not permitted.** Operators of hosted instances are responsible for moderating what's shared through their deployment. See [`LICENSE`](./LICENSE) for the full liability disclaimer.

---

## 👤 Credits

Built and maintained by:

- **Telegram:** [@Xioqui_xin](https://t.me/Xioqui_xin) (Xioqui) · [@TechnicalSerena](https://t.me/TechnicalSerena) (Technical 🕷️ Serena)
- **Instagram:** [@Prince572002](https://instagram.com/Prince572002) (Alka Music Status)

Please keep this credits section intact if you fork or redistribute this project.

---

## 📄 License

Released under a custom MIT-based license with an added liability disclaimer — see [`LICENSE`](./LICENSE). In short: free to use, modify, and self-host, but the original authors are not responsible for how you or your users use it, including any illegal use.
