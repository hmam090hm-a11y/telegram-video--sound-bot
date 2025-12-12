#!/usr/bin/env python3
"""
Telegram Downloader Bot (Webhook Version)
- يدعم رابط أو اسم أغنية (بحث تلقائي)
- تحميل فيديو وصوت mp3
- اشتراك إجباري
- Webhook يعمل على Render بدون مشاكل
"""

import os
import re
import logging
import tempfile
import shutil
import asyncio
from pathlib import Path

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

# -------------------- Logging --------------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- Settings --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # مثال: https://your-render-url.onrender.com/

if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("❌ يجب ضبط BOT_TOKEN و WEBHOOK_URL داخل Render")

FORCE_CHANNELS = config.FORCE_CHANNELS

BASE_TMP = Path(tempfile.gettempdir()) / "tgdl"
BASE_TMP.mkdir(parents=True, exist_ok=True)

# -------------------- Subscription --------------------
async def is_subscribed(user_id, context):
    for ch in FORCE_CHANNELS:
        try:
            ch_id = f"@{ch}" if not str(ch).startswith("@") else ch
            member = await context.bot.get_chat_member(ch_id, user_id)
            if member.status in ("left", "kicked"):
                return False
        except:
            return False
    return True

def force_sub_text():
    msg = "⚠️ للاستخدام يجب الاشتراك في القنوات:\n\n"
    for ch in FORCE_CHANNELS:
        msg += f"👉 https://t.me/{ch}\n"
    msg += "\nبعد الاشتراك أرسل /start"
    return msg

# -------------------- YouTube Search --------------------
def yt_search_sync(query):
    ydl_opts = {
        "default_search": "ytsearch1",
        "quiet": True,
        "noplaylist": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info and info["entries"]:
            return info["entries"][0].get("webpage_url")
        return None

async def yt_search(query):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, yt_search_sync, query)
    except:
        return None

# -------------------- Download --------------------
async def download_media(url, mode):
    tmpdir = Path(tempfile.mkdtemp(prefix="dl_", dir=str(BASE_TMP)))

    if mode == "video":
        opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True
        }
    else:  # audio
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }

    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))

    files = list(tmpdir.glob("*"))
    if files:
        return str(files[0]), info

    raise Exception("No file found!")

# -------------------- Handlers --------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return

    database.add_user(user.id)

    await update.message.reply_text(
        "🎉 أهلاً بك!\n"
        "أرسل رابط فيديو أو أكتب اسم أغنية/زامل وسيتم البحث تلقائياً.\n"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return

    if not text:
        await update.message.reply_text("أرسل رابط أو اسم أغنية.")
        return

    # لو مو رابط → بحث
    if not text.startswith("http://") and not text.startswith("https://"):
        await update.message.reply_text("🔎 جاري البحث...")
        url = await yt_search(text)
        if not url:
            await update.message.reply_text("❌ لا توجد نتائج.")
            return
    else:
        url = text

    context.user_data["url"] = url

    buttons = [
        [InlineKeyboardButton("🎬 فيديو", callback_data="video")],
        [InlineKeyboardButton("🎧 صوت MP3", callback_data="audio")]
    ]

    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode = query.data
    url = context.user_data.get("url")

    if not url:
        await query.edit_message_text("❌ أرسل الرابط مرة أخرى.")
        return

    await query.edit_message_text("⏳ جارٍ التحميل...")

    try:
        filepath, info = await download_media(url, mode)

        if mode == "video":
            await context.bot.send_video(query.message.chat_id, open(filepath, "rb"), caption=info.get("title"))
        else:
            await context.bot.send_audio(query.message.chat_id, open(filepath, "rb"), title=info.get("title"))

        await query.edit_message_text("✅ تم الإرسال.")
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ: {e}")

# -------------------- Main (Webhook) --------------------
def main():
    database.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_handler))

    import nest_asyncio
    nest_asyncio.apply()
    from aiohttp import web

    async def handle(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.update_queue.put(update)
        return web.Response(text="OK")

    async def run_webhook():
        web_app = web.Application()
        web_app.router.add_post("/", handle)

        runner = web.AppRunner(web_app)
        await runner.setup()

        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
        await site.start()

        await app.bot.set_webhook(WEBHOOK_URL)
        logger.info("🚀 Webhook Running at: " + WEBHOOK_URL)

        await app.initialize()
        await app.start()

        while True:
            await asyncio.sleep(3600)

    asyncio.run(run_webhook())

if __name__ == "__main__":
    main()
