#!/usr/bin/env python3
"""
Premium Telegram Bot (Webhook Version)
يدعم فيديو/صوت، اشتراك إجباري، VIP، أزرار Inline
"""

import os
import logging
import tempfile
import shutil
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

import yt_dlp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import config
import database

# ---------- Logging ----------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Settings ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # رابط Render العام
if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Set BOT_TOKEN and WEBHOOK_URL in Environment Variables.")

FORCE_CHANNELS = config.FORCE_CHANNELS
ADMIN_ID = getattr(config, "ADMIN_ID", None)
DAILY_LIMIT = getattr(config, "DAILY_LIMIT", 5)
VIP_LIMIT = getattr(config, "VIP_LIMIT", 99999)

BASE_TMP = Path(tempfile.gettempdir()) / "tg_premium_bot"
BASE_TMP.mkdir(parents=True, exist_ok=True)

# ---------- Utilities ----------
async def is_subscribed(user_id, context):
    for ch in FORCE_CHANNELS:
        try:
            member = await context.bot.get_chat_member(f"@{ch}", user_id)
            if member.status in ["left", "kicked"]:
                return False
        except:
            return False
    return True

def force_sub_text():
    txt = "⚠️ للاستخدام يجب الاشتراك في القنوات التالية:\n\n"
    for ch in FORCE_CHANNELS:
        txt += f"👉 https://t.me/{ch}\n"
    txt += "\nثم اضغط /start"
    return txt

def human_readable_size(n):
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024.0: return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

def can_download(user_id):
    database.add_user(user_id)
    user = database.get_user(user_id)
    if not user: return False, "خطأ بجلب بياناتك."
    _, downloads, vip_until, last_reset = user
    today = datetime.now().strftime("%Y-%m-%d")
    if last_reset != today:
        database.reset_daily_limit(user_id)
        downloads = 0
    vip_date = datetime.strptime(vip_until, "%Y-%m-%d") if vip_until else None
    limit = VIP_LIMIT if (vip_date and vip_date >= datetime.now()) else DAILY_LIMIT
    if downloads >= limit:
        return False, f"🥵 وصلت الحد اليومي ({limit}) — اشترك VIP لرفع الحد."
    return True, None

async def download_media(url, choice, quality="best"):
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
                "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}],
            }
        else:
            raise ValueError("choice must be 'video' or 'audio'")
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            filename = ydl.prepare_filename(info)
            return str(filename), info
    except Exception as e:
        logger.exception("Download failed: %s", e)
        raise
    finally:
        pass

# ---------- Handlers ----------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return
    database.add_user(user.id)
    await update.message.reply_text(
        "🎉 أهلاً بك في بوت التحميل الاحترافي!\n"
        "📥 أرسل أي رابط فيديو/صوت وسيظهر لك زر التحميل.\n"
        "الأوامر: /me /vipstatus /help"
    )

async def me_handler(update, context):
    user = update.effective_user
    info = database.get_user(user.id)
    if not info:
        await update.message.reply_text("لم تُسجل بعد. أرسل رابطاً للبوت.")
        return
    user_id, downloads, vip_until, last_reset = info
    text = f"📌 معلوماتك:\n- ID: {user_id}\n- تحميلات اليوم: {downloads}\n- VIP حتى: {vip_until or 'غير مفعل'}"
    await update.message.reply_text(text)

async def handle_link(update, context):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return
    url = (update.message.text or "").strip()
    if not url:
        await update.message.reply_text("أرسل رابط صالح.")
        return
    ok, reason = can_download(user.id)
    if not ok:
        await update.message.reply_text(reason)
        return
    context.user_data["last_link"] = url
    buttons = []
    if "youtu" in url:
        buttons = [
            [InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="video")],
            [InlineKeyboardButton("🎧 تحميل الصوت MP3", callback_data="audio")]
        ]
    else:
        buttons = [[InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="video")]]
    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    ok, reason = can_download(user.id)
    if not ok:
        await query.edit_message_text(reason)
        return
    data = query.data
    url = context.user_data.get("last_link")
    if not url:
        await query.edit_message_text("❌ الرابط غير موجود.")
        return
    await query.edit_message_text("⏳ جاري التحميل...")
    try:
        if data == "video":
            filepath, info = await download_media(url, "video")
            await context.bot.send_video(query.message.chat_id, open(filepath, "rb"), caption=info.get("title","-"))
        elif data == "audio":
            filepath, info = await download_media(url, "audio")
            await context.bot.send_audio(query.message.chat_id, open(filepath, "rb"), title=info.get("title","-"))
        database.increment_downloads(user.id)
        await query.edit_message_text("✅ تم الإرسال.")
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ: {e}")

# ---------- App ----------
def main():
    database.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("me", me_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Webhook setup
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
        print("🚀 Webhook Bot Running...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        while True: await asyncio.sleep(3600)

    asyncio.run(start_webhook())

if __name__ == "__main__":
    main()
