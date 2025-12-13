#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import nest_asyncio
from aiohttp import web

# ================== إعدادات ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # مثال: https://xxxx.onrender.com/

if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("❌ BOT_TOKEN أو WEBHOOK_URL غير موجود")

BASE_TMP = Path(tempfile.gettempdir()) / "tg_webhook_bot"
BASE_TMP.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# ================== أدوات ==================
def is_url(text: str) -> bool:
    return re.match(r"^https?://", text) is not None


def yt_search_sync(query: str):
    """بحث في يوتيوب باستخدام cookies"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "skip_download": True,
        "cookiefile": "cookies.txt",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if info and "entries" in info and info["entries"]:
            return info["entries"][0]["webpage_url"]
    return None


async def yt_search(query: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, yt_search_sync, query)


async def download_media(url: str, mode: str):
    tmp = Path(tempfile.mkdtemp(dir=BASE_TMP))

    ydl_opts = {
        "outtmpl": str(tmp / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cookiefile": "cookies.txt",
    }

    if mode == "video":
        ydl_opts["format"] = "bestvideo+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"
    else:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, True))

    files = list(tmp.glob("*"))
    files.sort(key=lambda f: f.stat().st_size, reverse=True)
    return files[0], info


# ================== Handlers ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎧 أرسل رابط يوتيوب أو اسم أغنية / زامل\n"
        "وسيظهر لك خيار التحميل"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not is_url(text):
        await update.message.reply_text("🔍 جاري البحث في يوتيوب...")
        url = await yt_search(text)
        if not url:
            await update.message.reply_text("❌ لا توجد نتائج")
            return
    else:
        url = text

    context.user_data["url"] = url

    keyboard = [
        [InlineKeyboardButton("🎬 تحميل فيديو", callback_data="video")],
        [InlineKeyboardButton("🎧 تحميل صوت MP3", callback_data="audio")],
    ]

    await update.message.reply_text(
        "اختر نوع التحميل:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    url = context.user_data.get("url")
    if not url:
        await q.edit_message_text("❌ أعد إرسال الطلب")
        return

    await q.edit_message_text("⏳ جاري التحميل والمعالجة...")

    try:
        file, info = await download_media(url, q.data)

        if q.data == "video":
            await context.bot.send_video(
                q.message.chat_id,
                open(file, "rb"),
                caption=info.get("title", "")
            )
        else:
            await context.bot.send_audio(
                q.message.chat_id,
                open(file, "rb"),
                title=info.get("title", "")
            )

        await q.edit_message_text("✅ تم الإرسال بنجاح")

    except Exception as e:
        await q.edit_message_text(f"❌ خطأ: {e}")

    finally:
        shutil.rmtree(file.parent, ignore_errors=True)


# ================== Webhook Server ==================
def main():
    nest_asyncio.apply()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback))

    async def webhook_handler(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.update_queue.put(update)
        return web.Response(text="ok")

    web_app = web.Application()
    web_app.router.add_post("/", webhook_handler)

    async def run():
        await app.initialize()
        await app.start()
        await app.bot.set_webhook(WEBHOOK_URL)

        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            "0.0.0.0",
            int(os.getenv("PORT", "10000"))
        )
        await site.start()

        print("🚀 Webhook Bot Running")
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run())


if __name__ == "__main__":
    main()
