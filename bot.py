#!/usr/bin/env python3
"""
bot.py - Premium Telegram Video/Audio Downloader Bot
Requires: config.py, database.py
Designed to run on Termux / VPS / Render / Heroku-like services.
"""

import os
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatAction,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Local modules (from earlier step)
import config
import database

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Settings ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN environment variable before running.")

FORCE_CHANNELS = config.FORCE_CHANNELS
ADMIN_ID = getattr(config, "ADMIN_ID", None)
DAILY_LIMIT = getattr(config, "DAILY_LIMIT", 5)
VIP_LIMIT = getattr(config, "VIP_LIMIT", 99999)

# temp folder base
BASE_TMP = Path(tempfile.gettempdir()) / "tg_premium_bot"
BASE_TMP.mkdir(parents=True, exist_ok=True)

# ---------- Utilities ----------
async def is_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check that user is member of all FORCE_CHANNELS."""
    for ch in FORCE_CHANNELS:
        try:
            # try with @ if needed
            ch_id = f"@{ch}" if not str(ch).startswith("@") else ch
            member = await context.bot.get_chat_member(ch_id, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            logger.warning("Subscription check failed for %s: %s", ch, e)
            return False
    return True

def force_sub_text() -> str:
    txt = "⚠️ للاستخدام، يجب الاشتراك في القنوات التالية أولاً:\n\n"
    for ch in FORCE_CHANNELS:
        txt += f"👉 https://t.me/{ch}\n"
    txt += "\nثم اضغط /start"
    return txt

def human_readable_size(n):
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

# ---------- YT-DLP helper (runs in executor) ----------
def ytdlp_download_blocking(url: str, outdir: str, ydl_opts: dict):
    """Blocking call to download using yt-dlp. Returns filepath and info dict."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # prepare filename
        filename = ydl.prepare_filename(info)
        # yt-dlp may change extension when postprocessors run; find actual file
        # return actual existing file path
        possible = list(Path(outdir).glob(f"{info.get('id','*')}*"))
        if possible:
            return str(possible[0]), info
        return filename, info

async def download_media(url: str, choice: str, quality: str = "best"):
    """Download video or audio and return local path and mime type/details."""
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
            loop = asyncio.get_event_loop()
            filepath, info = await loop.run_in_executor(None, ytdlp_download_blocking, url, str(tmpdir), ydl_opts)
            return str(filepath), "video", info
        elif choice == "audio":
            # extract audio mp3
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
            loop = asyncio.get_event_loop()
            filepath, info = await loop.run_in_executor(None, ytdlp_download_blocking, url, str(tmpdir), ydl_opts)
            # ensure .mp3 extension
            mp3s = list(tmpdir.glob("*.mp3"))
            if mp3s:
                return str(mp3s[0]), "audio", info
            return str(filepath), "audio", info
        else:
            raise ValueError("choice must be 'video' or 'audio'")
    except Exception as e:
        logger.exception("Download failed: %s", e)
        raise
    finally:
        # Note: cleanup is caller responsibility after sending
        pass

# ---------- Rate limiting / quota ----------
def can_download(user_id: int):
    """Check user's daily limit and VIP status."""
    database.add_user(user_id)
    user = database.get_user(user_id)
    if not user:
        return False, "حصل خطأ في جلب بيانات المستخدم."

    _, downloads, vip_until, last_reset = user  # columns in order: user_id, downloads, vip_until, last_reset (depends on schema)
    # Normalize vip_until
    if vip_until:
        try:
            vip_date = datetime.strptime(vip_until, "%Y-%m-%d")
        except:
            vip_date = None
    else:
        vip_date = None

    # reset daily if needed
    today = datetime.now().strftime("%Y-%m-%d")
    if last_reset != today:
        database.reset_daily_limit(user_id)
        downloads = 0

    limit = VIP_LIMIT if (vip_date and vip_date >= datetime.now()) else DAILY_LIMIT
    if downloads >= limit:
        return False, f"🥵 وصلت الحد المسموح اليوم ({limit}) — اشترك VIP لرفع الحد."
    return True, None

# ---------- Handlers ----------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return
    database.add_user(user.id)
    await update.message.reply_text(
        "🎉 أهلاً في بوت التحميل الاحترافي!\n\n"
        "أرسل رابط فيديو أو استخدم الأوامر:\n"
        "/start - رسالة البداية\n"
        "/me - بياناتك\n"
        "/vipstatus - حالة VIP\n"
        "/help - المساعدة\n\n"
        "عند إرسال رابط ستظهر لك أزرار اختيار التحميل."
    )

async def me_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    info = database.get_user(user.id)
    if not info:
        await update.message.reply_text("لم تُسجل بعد. أرسل شيء للبوت ليتم التسجيل.")
        return
    user_id, downloads, vip_until, last_reset = info
    text = f"📌 معلوماتك:\n- ID: {user_id}\n- تحميلات اليوم: {downloads}\n- VIP حتى: {vip_until or 'غير مفعل'}"
    await update.message.reply_text(text)

async def vipstatus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    info = database.get_user(user.id)
    if not info:
        await update.message.reply_text("أنت غير مسجل بعد. أرسل رابط أو رسالة للتسجيل.")
        return
    user_id, downloads, vip_until, last_reset = info
    await update.message.reply_text(f"حالة VIP: {vip_until or 'غير مفعل'}\nتحميلات اليوم: {downloads}")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 تعليمات:\n"
        "1) أرسل رابط فيديو من YouTube/TikTok/Instagram/Facebook/Twitter...\n"
        "2) اختر 'تحميل فيديو' أو 'تحميل صوت' (إن أمكن).\n"
        "أوامر المسؤول: /broadcast <msg> , /vipadd <user_id> <days> , /stats\n"
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_subscribed(user.id, context):
        await update.message.reply_text(force_sub_text())
        return

    url = (update.message.text or "").strip()
    if not url:
        await update.message.reply_text("أرسل رابط صالح.")
        return

    # Rate limit check
    ok, reason = can_download(user.id)
    if not ok:
        await update.message.reply_text(reason)
        return

    # Store link in user_data
    context.user_data["last_link"] = url

    # Buttons: video/audio + quality choices for YouTube
    buttons = []
    if "youtu" in url or "youtube.com" in url or "youtu.be" in url:
        buttons = [
            [InlineKeyboardButton("🎬 تحميل الفيديو (أفضل جودة)", callback_data="video:best")],
            [InlineKeyboardButton("📺 تحميل فيديو 1080p", callback_data="video:1080")],
            [InlineKeyboardButton("📺 تحميل فيديو 720p", callback_data="video:720")],
            [InlineKeyboardButton("🎧 تنزيل الصوت (MP3)", callback_data="audio:mp3")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton("🎬 تحميل الفيديو", callback_data="video:best")]
        ]

    await update.message.reply_text("اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(buttons))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    # Re-check subscription & rate-limit before heavy work
    if not await is_subscribed(user.id, context):
        await query.edit_message_text(force_sub_text())
        return

    ok, reason = can_download(user.id)
    if not ok:
        await query.edit_message_text(reason)
        return

    data = query.data  # e.g. "video:1080" or "audio:mp3"
    if ":" in data:
        action, param = data.split(":", 1)
    else:
        action, param = data, None

    url = context.user_data.get("last_link")
    if not url:
        await query.edit_message_text("❌ رابط غير موجود أو منتهي الصلاحية. أعد الإرسال.")
        return

    # update message
    await query.edit_message_text("⏳ جاري التحميل والمعالجة، انتظر من فضلك...")

    tmp_dir = Path(tempfile.mkdtemp(prefix="dl_", dir=str(BASE_TMP)))
    try:
        # determine choice
        if action == "video":
            quality = "best"
            if param == "1080":
                quality = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
            elif param == "720":
                quality = "bestvideo[height<=720]+bestaudio/best[height<=720]"
            elif param == "best":
                quality = "bestvideo+bestaudio/best"
            filepath, kind, info = await download_media(url, "video", quality)
            size = Path(filepath).stat().st_size
            readable = human_readable_size(size)
            caption = f"📥 تم التحميل: {info.get('title','-')}\nحجم الملف: {readable}"
            # send depending on kind and size
            try:
                # send as video if mp4
                await context.bot.send_chat_action(query.message.chat_id, ChatAction.UPLOAD_VIDEO)
                await context.bot.send_video(query.message.chat_id, open(filepath, "rb"), caption=caption)
            except Exception:
                # fallback send as document
                await context.bot.send_document(query.message.chat_id, open(filepath, "rb"), caption=caption)
        elif action == "audio":
            # param may be mp3
            filepath, kind, info = await download_media(url, "audio")
            size = Path(filepath).stat().st_size
            readable = human_readable_size(size)
            caption = f"🎧 تم تحميل الصوت: {info.get('title','-')}\nحجم الملف: {readable}"
            await context.bot.send_chat_action(query.message.chat_id, ChatAction.UPLOAD_AUDIO)
            await context.bot.send_audio(query.message.chat_id, open(filepath, "rb"), title=info.get("title"))
        else:
            await query.edit_message_text("خيار غير معروف.")
            return

        # increment user's downloads
        database.increment_downloads(user.id)

        await query.edit_message_text("✅ تم الإنجاز — تم إرسال الملف إليك.")
    except Exception as e:
        logger.exception("Processing failed: %s", e)
        await query.edit_message_text(f"❌ حدث خطأ أثناء التحميل: {e}")
    finally:
        # cleanup temp dir
        try:
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
            # also attempt to remove yt-dlp temp files inside BASE_TMP
        except Exception:
            pass

# ---------- Admin commands ----------
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("أمر مخصص للأدمن فقط.")
        return
    # simple stats: count users
    # For simplicity, we query DB file directly
    conn = None
    try:
        conn = __import__("sqlite3").connect(database.DB)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        cur.execute("SELECT SUM(downloads) FROM users")
        total_dl = cur.fetchone()[0] or 0
        await update.message.reply_text(f"📊 إحصائيات:\n- عدد المستخدمين: {users_count}\n- إجمالي التحميلات: {total_dl}")
    finally:
        if conn:
            conn.close()

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("أمر مخصص للأدمن فقط.")
        return
    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("استخدم: /broadcast رسالة")
        return
    # fetch all users and send message (naive)
    conn = __import__("sqlite3").connect(database.DB)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    conn.close()
    sent = 0
    for (uid,) in rows:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue
    await update.message.reply_text(f"تم الارسال لـ {sent} مستخدم/مستخدمين")

async def admin_vipadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("أمر مخصص للأدمن فقط.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("استخدم: /vipadd <user_id> <days>")
        return
    try:
        uid = int(context.args[0])
        days = int(context.args[1])
    except:
        await update.message.reply_text("صيغة خطأ.")
        return
    database.add_user(uid)
    database.activate_vip(uid, days)
    await update.message.reply_text(f"✅ تم تفعيل VIP للمستخدم {uid} لمدة {days} يومًا.")

# ---------- App init ----------
def main():
    # init db
    database.init_db()
    # build app
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("me", me_handler))
    app.add_handler(CommandHandler("vipstatus", vipstatus_handler))
    app.add_handler(CommandHandler("help", help_handler))

    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("vipadd", admin_vipadd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("🚀 Premium Bot Started")
    app.run_polling()

if __name__ == "__main__":
    main()
