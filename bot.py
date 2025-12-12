#!/usr/bin/env python3
import os
import logging
import asyncio
import tempfile
from pathlib import Path
import shutil
import re

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://yourservice.onrender.com

if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("❌ BOT_TOKEN or WEBHOOK_URL missing")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary folder
BASE_TMP = Path(tempfile.gettempdir()) / "bot_tmp"
BASE_TMP.mkdir(exist_ok=True)

# ---------- SEARCH ----------
def yt_search_sync(query):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "skip_download": True,
        "noplaylist": True,
        "cookies": "cookies.txt",  # ← مهم جدًا
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if not info:
            return None
        if "entries" in info:
            return info["entries"][0]["webpage_url"]
        return info.get("webpage_url")


async def yt_search(query):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, yt_search_sync, query)
    except:
        return None

# ---------- DOWNLOAD ----------
async def download_media(url, mode):
    tmp = Path(tempfile.mkdtemp(prefix="dl_", dir=str(BASE_TMP)))

    ydl_opts = {
        "outtmpl": str(tmp / "%(title)s.%(ext)s"),
        "cookies": "cookies.txt",  # ← هنا أيضًا مهم
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    if mode == "video":
        ydl_opts["format"] = "bestvideo+bestaudio/best"
    else:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))

    files = list(tmp.glob("*"))
    files.sort(key=lambda x: x.stat().st_size, reverse=True)

    return str(files[0]), info


# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط فيديو أو اكتب اسم أغنية 🎵")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not re.match(r"^https?://", text):
        await update.message.reply_text("🔎 جاري البحث…")
        url = await yt_search(text)
        if not url:
            return await update.message.reply_text("❌ لا توجد نتائج. جرّب اسم آخر.")
    else:
        url = text

    context.user_data["url"] = url
    btn = [
        [InlineKeyboardButton("🎬 فيديو", callback_data="video")],
        [InlineKeyboardButton("🎧 صوت MP3", callback_data="audio")],
    ]

    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(btn))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    mode = q.data
    url = context.user_data.get("url")

    await q.edit_message_text("⏳ جاري التحميل…")

    try:
        file, info = await download_media(url, mode)
        title = info.get("title", "File")

        if mode == "video":
            await q.message.reply_video(open(file, "rb"), caption=title)
        else:
            await q.message.reply_audio(open(file, "rb"), title=title)

        await q.edit_message_text("✅ تم الإرسال.")
    except Exception as e:
        await q.edit_message_text(f"❌ خطأ: {e}")


# ---------- WEBHOOK ----------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback))

    from aiohttp import web
    import nest_asyncio
    nest_asyncio.apply()

    async def handler(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.update_queue.put(update)
        return web.Response(text="OK")

    runner = web.AppRunner(web.Application())
    async def run_webhook():
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
        await site.start()
        await app.bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook started")
        await app.initialize()
        await app.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run_webhook())

if __name__ == "__main__":
    main()
