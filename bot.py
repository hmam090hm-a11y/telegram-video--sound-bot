#!/usr/bin/env python3
# Premium Telegram Downloader Bot (with YouTube cookies support)

import os
import re
import logging
import tempfile
import shutil
import asyncio
from pathlib import Path
from datetime import datetime

import yt_dlp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
import database

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("BOT_TOKEN and WEBHOOK_URL are required.")

FORCE_CHANNELS = config.FORCE_CHANNELS
BASE_TMP = Path(tempfile.gettempdir()) / "tg_bot"
BASE_TMP.mkdir(parents=True, exist_ok=True)

COOKIES_FILE = "cookies.txt"   # <— مهم جداً


# ------------------- Utilities -------------------

async def is_subscribed(user_id, context):
    for ch in FORCE_CHANNELS:
        try:
            ch_id = f"@{ch}" if not str(ch).startswith("@") else ch
            m = await context.bot.get_chat_member(ch_id, user_id)
            if m.status in ("left", "kicked"):
                return False
        except:
            return False
    return True


def force_sub_text():
    return "⚠️ يجب الاشتراك في القنوات:\n\n" + "\n".join(
        [f"👉 https://t.me/{ch}" for ch in FORCE_CHANNELS]
    ) + "\n\nثم أعد /start"


def yt_search_sync(query):
    opts = {
        "quiet": True,
        "default_search": "ytsearch1",
        "skip_download": True,
        "cookiefile": COOKIES_FILE,   # <— استخدم الكوكيز هنا
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info and info["entries"]:
            return info["entries"][0].get("webpage_url")
        return info.get("webpage_url")


async def yt_search(query):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, yt_search_sync, query)


# ------------------- Download -------------------

async def download_media(url, choice):
    tmpdir = Path(tempfile.mkdtemp(prefix="dl_", dir=str(BASE_TMP)))

    if choice == "video":
        opts = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "cookiefile": COOKIES_FILE,      # <— أهم سطر
            "quiet": True,
        }
    else:
        opts = {
            "format": "bestaudio",
            "cookiefile": COOKIES_FILE,      # <— مهم للصوت أيضاً
            "quiet": True,
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
        }

    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))

        files = list(tmpdir.glob("*"))
        files.sort(key=lambda p: p.stat().st_size, reverse=True)

        return str(files[0]), info


# ------------------- Handlers -------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user

    if not await is_subscribed(u.id, context):
        await update.message.reply_text(force_sub_text())
        return

    database.add_user(u.id)
    await update.message.reply_text("🎉 أهلاً بك! أرسل رابط أو اسم أغنية…")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user

    if not await is_subscribed(u.id, context):
        await update.message.reply_text(force_sub_text())
        return

    text = update.message.text.strip()

    if not re.match(r"^https?://", text):
        await update.message.reply_text("🔍 جاري البحث…")
        url = await yt_search(text)
        if not url:
            await update.message.reply_text("❌ لا توجد نتائج.")
            return
    else:
        url = text

    context.user_data["url"] = url

    btns = [
        [InlineKeyboardButton("🎬 فيديو", callback_data="video")],
        [InlineKeyboardButton("🎧 صوت MP3", callback_data="audio")]
    ]

    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(btns))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    url = context.user_data.get("url")
    if not url:
        await q.edit_message_text("❌ أعد إرسال الرابط.")
        return

    await q.edit_message_text("⏳ جاري التحميل…")

    try:
        filepath, info = await download_media(url, q.data)

        if q.data == "video":
            await q.message.chat.send_video(open(filepath, "rb"), caption=info.get("title", "-"))
        else:
            await q.message.chat.send_audio(open(filepath, "rb"), title=info.get("title", "-"))

        await q.edit_message_text("✅ تم الإرسال!")

    except Exception as e:
        await q.edit_message_text(f"❌ خطأ: {e}")


# ------------------- Webhook -------------------

def main():
    database.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))

    import nest_asyncio
    nest_asyncio.apply()
    from aiohttp import web

    async def handle(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.update_queue.put(update)
        return web.Response(text="OK")

    runner = web.AppRunner(web.Application())
    async def start_webhook():
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
        await site.start()
        await app.bot.set_webhook(WEBHOOK_URL)
        logger.info("🚀 Webhook Running…")
        await app.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(start_webhook())


if __name__ == "__main__":
    main()
