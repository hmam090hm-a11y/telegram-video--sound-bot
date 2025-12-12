#!/usr/bin/env python3
"""
Premium Telegram Bot (Webhook Version)
- يدعم رابط أو اسم أغنية (بحث على YouTube تلقائياً)
- تحميل فيديو أو صوت (mp3)
- اشتراك إجباري بالقنوات من config.py
- يعمل كـ Webhook (مناسب للـ Render Web Service)
"""

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

# ---------- Logging ----------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Settings ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Example: https://your-render-app.onrender.com/
if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Set BOT_TOKEN and WEBHOOK_URL environment variables.")

FORCE_CHANNELS = config.FORCE_CHANNELS
ADMIN_ID = getattr(config, "ADMIN_ID", None)

# We remove hard daily limit: allow downloads (but still record them)
DAILY_LIMIT = getattr(config, "DAILY_LIMIT", None)
VIP_LIMIT = getattr(config, "VIP_LIMIT", None)

BASE_TMP = Path(tempfile.gettempdir()) / "tg_premium_bot"
BASE_TMP.mkdir(parents=True, exist_ok=True)

# ---------- Utilities ----------
async def is_subscribed(user_id, context):
    """Check membership in required channels."""
    for ch in FORCE_CHANNELS:
        try:
            ch_id = f"@{ch}" if not str(ch).startswith("@") else ch
            member = await context.bot.get_chat_member(ch_id, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            logger.warning("Subscription check failed for %s: %s", ch, e)
            return False
    return True

def force_sub_text():
    txt = "⚠️ للاستخدام يجب الاشتراك في القنوات التالية:\n\n"
    for ch in FORCE_CHANNELS:
        txt += f"👉 https://t.me/{ch}\n"
    txt += "\nثم أعد /start"
    return txt

def human_readable_size(n):
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

def can_download(user_id):
    """
    Currently allow all downloads (no daily limit).
    We still ensure user exists in DB and return True.
    """
    database.add_user(user_id)
    return True, None

# ---------- YouTube search helper ----------
def yt_search_sync(query):
    """
    Synchronous helper using yt_dlp to perform ytsearch and return first result URL.
    Called inside executor.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if not info:
            return None
        # ytsearch1 returns dict with 'entries'
        if "entries" in info and info["entries"]:
            first = info["entries"][0]
            return first.get("webpage_url")
        # sometimes extract_info on a direct video returns webpage_url
        return info.get("webpage_url")

async def yt_search(query):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, yt_search_sync, query)
    except Exception as e:
        logger.exception("yt_search failed: %s", e)
        return None

# ---------- Download helper ----------
async def download_media(url: str, choice: str, quality: str = "best"):
    """
    choice: "video" or "audio"
    returns: filepath, info
    Caller must handle sending file and cleanup.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="tgdl_", dir=str(BASE_TMP)))
    try:
        if choice == "video":
            ydl_opts = {
                "format": quality if quality != "best" else "bestvideo+bestaudio/best",
                "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
        elif choice == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
        else:
            raise ValueError("choice must be 'video' or 'audio'")

        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            # find produced file (yt-dlp may change extension)
            # try to find first file in tmpdir
            files = list(tmpdir.glob("*"))
            files = [p for p in files if p.is_file()]
            if files:
                # choose largest file
                files.sort(key=lambda p: p.stat().st_size, reverse=True)
                return str(files[0]), info
            filename = ydl.prepare_filename(info)
            return str(filename), info
    except Exception as e:
        logger.exception("download_media failed: %s", e)
        raise
    finally:
        # cleanup is left to caller to allow sending file before deletion
        pass

# ---------- Handlers ----------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return
    database.add_user(user.id)
    await update.message.reply_text(
        "🎉 أهلاً بك في بوت التحميل!\n"
        "✳️ أرسل رابط الفيديو أو فقط أكتب اسم الأغنية/الزامل.\n"
        "سيقوم البوت بالبحث وإظهار أزرار التحميل."
    )

async def me_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    info = database.get_user(user.id)
    if not info:
        await update.message.reply_text("لم تُسجل بعد. أرسل رابطًا أو اسمًا للبوت.")
        return
    user_id, downloads, vip_until, last_reset = info
    text = f"📌 معلوماتك:\n- ID: {user_id}\n- تحميلات مسجلة: {downloads}\n- VIP حتى: {vip_until or 'غير مفعل'}"
    await update.message.reply_text(text)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("✳️ أرسل رابطًا أو اكتب اسم الأغنية/الزامل.")
        return

    # إذا النص ليس رابط http -> اعتبره استعلام بحث
    if not re.match(r"^https?://", text, re.IGNORECASE):
        await update.message.reply_text("🔎 جارٍ البحث في YouTube...")
        found = await yt_search(text)
        if not found:
            await update.message.reply_text("❌ لم أجد نتائج. جرّب اسمًا آخر أو أرسل رابطًا مباشرًا.")
            return
        url = found
    else:
        url = text

    ok, reason = can_download(user.id)
    if not ok:
        await update.message.reply_text(reason)
        return

    context.user_data["last_link"] = url

    # عرض أزرار التحميل
    buttons = []
    if "youtu" in url:
        buttons = [
            [InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="video")],
            [InlineKeyboardButton("🎧 تحميل الصوت MP3", callback_data="audio")]
        ]
    else:
        buttons = [[InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="video")]]

    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    ok, reason = can_download(user.id)
    if not ok:
        await query.edit_message_text(reason)
        return

    data = query.data  # "video" or "audio"
    url = context.user_data.get("last_link")
    if not url:
        await query.edit_message_text("❌ الرابط غير موجود. أعد إرساله.")
        return

    await query.edit_message_text("⏳ جاري التحميل والمعالجة...")

    tmpdir = Path(tempfile.mkdtemp(prefix="send_", dir=str(BASE_TMP)))
    try:
        if data == "video":
            filepath, info = await download_media(url, "video")
            size = Path(filepath).stat().st_size
            await context.bot.send_chat_action(query.message.chat_id, "upload_video")
            # إرسال كـ video إن كان mp4 أو مناسب
            try:
                await context.bot.send_video(query.message.chat_id, open(filepath, "rb"), caption=info.get("title","-"))
            except Exception:
                await context.bot.send_document(query.message.chat_id, open(filepath, "rb"), caption=info.get("title","-"))
        else:  # audio
            filepath, info = await download_media(url, "audio")
            await context.bot.send_chat_action(query.message.chat_id, "upload_audio")
            await context.bot.send_audio(query.message.chat_id, open(filepath, "rb"), title=info.get("title","-"))

        # سجل التحميل
        try:
            database.increment_downloads(user.id)
        except Exception:
            pass

        await query.edit_message_text("✅ تم الإرسال.")
    except Exception as e:
        logger.exception("callback_handler error: %s", e)
        await query.edit_message_text(f"❌ حدث خطأ أثناء التحميل: {e}")
    finally:
        try:
            shutil.rmtree(str(tmpdir), ignore_errors=True)
        except:
            pass

# ---------- Webhook server bootstrap ----------
def main():
    database.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("me", me_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Webhook setup using aiohttp to receive POSTs from Telegram
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
        site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", "10000")))
        await site.start()
        # set webhook to Telegram
        await app.bot.set_webhook(WEBHOOK_URL)
        logger.info("🚀 Webhook Bot Running...")
        await app.initialize()
        await app.start()
        # keep process alive
        while True:
            await asyncio.sleep(3600)

    asyncio.run(start_webhook())

if __name__ == "__main__":
    main()
