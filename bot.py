#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Telegram Video & Audio Downloader Bot (Webhook – Render Ready)

يدعم رابط أو اسم أغنية (بحث YouTube)

تحميل فيديو أو صوت MP3

يدعم cookies.txt لتجاوز قيود YouTube

Webhook صحيح 100% (POST /) """


import os import re import asyncio import logging import tempfile import shutil from pathlib import Path

import yt_dlp from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup from telegram.ext import ( ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, )

-------------------- CONFIG --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN") WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # مثال: https://your-app.onrender.com/ PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN or not WEBHOOK_URL: raise RuntimeError("BOT_TOKEN و WEBHOOK_URL لازم يكونوا موجودين")

COOKIES_FILE = "cookies.txt"  # لازم يكون في نفس مجلد bot.py BASE_TMP = Path(tempfile.gettempdir()) / "tg_dl_bot" BASE_TMP.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO) logger = logging.getLogger(name)

-------------------- HELPERS --------------------

def is_url(text: str) -> bool: return bool(re.match(r"^https?://", text, re.I))

def ydl_opts_base(): opts = { "quiet": True, "no_warnings": True, "noplaylist": True, } if Path(COOKIES_FILE).exists(): opts["cookiefile"] = COOKIES_FILE return opts

async def yt_search(query: str) -> str | None: def _search(): opts = ydl_opts_base() opts.update({"default_search": "ytsearch1", "skip_download": True}) with yt_dlp.YoutubeDL(opts) as ydl: info = ydl.extract_info(query, download=False) if info and "entries" in info and info["entries"]: return info["entries"][0].get("webpage_url") return None

loop = asyncio.get_event_loop()
return await loop.run_in_executor(None, _search)

async def download_media(url: str, kind: str) -> str: tmpdir = Path(tempfile.mkdtemp(prefix="dl_", dir=str(BASE_TMP)))

opts = ydl_opts_base()
if kind == "video":
    opts.update({
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
    })
else:
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    })

def _dl():
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    files = list(tmpdir.glob("*"))
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return str(files[0])

loop = asyncio.get_event_loop()
return await loop.run_in_executor(None, _dl)

-------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text( "🎬 أرسل رابط الفيديو أو اكتب اسم الأغنية/الزامل مباشرة" )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE): text = update.message.text.strip()

if not is_url(text):
    await update.message.reply_text("🔍 جارٍ البحث...")
    url = await yt_search(text)
    if not url:
        await update.message.reply_text("❌ لا توجد نتائج")
        return
else:
    url = text

context.user_data["url"] = url

buttons = [[
    InlineKeyboardButton("🎬 فيديو", callback_data="video"),
    InlineKeyboardButton("🎧 MP3", callback_data="audio"),
]]

await update.message.reply_text(
    "اختر نوع التحميل:",
    reply_markup=InlineKeyboardMarkup(buttons)
)

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE): q = update.callback_query await q.answer()

url = context.user_data.get("url")
if not url:
    await q.edit_message_text("❌ أعد إرسال الرابط")
    return

kind = q.data
await q.edit_message_text("⏳ جاري التحميل...")

try:
    path = await download_media(url, kind)
    with open(path, "rb") as f:
        if kind == "video":
            await context.bot.send_video(q.message.chat_id, f)
        else:
            await context.bot.send_audio(q.message.chat_id, f)
    await q.edit_message_text("✅ تم الإرسال")
except Exception as e:
    await q.edit_message_text(f"❌ خطأ: {e}")
finally:
    try:
        shutil.rmtree(Path(path).parent, ignore_errors=True)
    except Exception:
        pass

-------------------- WEBHOOK --------------------

def main(): app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(CallbackQueryHandler(callback))

from aiohttp import web
import nest_asyncio

nest_asyncio.apply()

async def handle_webhook(request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.update_queue.put(update)
    return web.Response(text="OK")

web_app = web.Application()
web_app.router.add_post("/", handle_webhook)

async def run():
    await app.initialize()
    await app.bot.set_webhook(WEBHOOK_URL)
    await app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("🚀 Bot is running with webhook")
    while True:
        await asyncio.sleep(3600)

asyncio.run(run())

if name == "main": main()
