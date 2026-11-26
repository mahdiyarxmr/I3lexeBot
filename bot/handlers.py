"""Telegram update handlers for the i3l.exe bot.

This is a faithful, behaviour-preserving port of the original polling bot in
``main.py``. Every handler, message, keyboard and business rule is kept. The
only changes are:

* database access is now async and goes through PostgreSQL (``db.*`` helpers),
* ``context.user_data`` conversation state is now persistent (``state``),
* anti-spam counters are now persistent (``rate_limiter``).
"""

from __future__ import annotations

import copy
import logging
import random
import re

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .runtime import AUTO_EDIT_EVERY, OWNER_ID, db, rate_limiter, state
from .ui import btn, e, safe

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Users, media and helpers
# ---------------------------------------------------------------------------
async def ensure_user(user) -> None:
    is_owner = user.id == OWNER_ID
    role = "Owner" if is_owner else "Member"
    color = "Owner" if is_owner else "blue"
    await db.execute(
        "INSERT INTO users(user_id,username,first_name,role,color) VALUES(%s,%s,%s,%s,%s) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
        "first_name=excluded.first_name, "
        "role=CASE WHEN users.user_id=%s THEN 'Owner' ELSE users.role END, "
        "color=CASE WHEN users.user_id=%s THEN 'Owner' ELSE users.color END",
        (user.id, user.username, user.first_name, role, color, OWNER_ID, OWNER_ID),
    )


async def media_rows(kinds, approved=1):
    marks = ",".join("%s" for _ in kinds)
    return await db.fetchall(
        f"SELECT id,file_id,kind,sender_id,username FROM media "
        f"WHERE kind IN ({marks}) AND approved=%s",
        (*kinds, approved),
    )


async def vote_counts(media_id):
    row = await db.fetchone(
        "SELECT SUM(CASE WHEN vote=1 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END) FROM votes WHERE media_id=%s",
        (media_id,),
    )
    return row[0] or 0, row[1] or 0


async def vote_buttons(media_id):
    up, down = await vote_counts(media_id)
    return InlineKeyboardMarkup([
        [btn(str(up), f"vote:{media_id}:1", "like", "success"),
         btn(str(down), f"vote:{media_id}:-1", "dislike", "danger")]
    ])


def review_buttons(media_id):
    return InlineKeyboardMarkup([
        [btn("تأیید", f"review:{media_id}:1", "approve", "success"),
         btn("رد", f"review:{media_id}:0", "reject", "danger")]
    ])


def main_menu(is_owner=False, is_admin=False):
    rows = [
        [btn("پروفایل من", "menu:profile", "profile"), btn("ادیت", "menu:edit", "edit")],
        [btn("موزیک", "menu:music", "music"), btn("گالری عکس", "menu:photo", "photo")],
        [btn("رنک", "menu:ranks", "rank"), btn("آمار", "menu:stats", "stats")],
        [btn("راهنما", "menu:help", "help"), btn("بستن", "menu:close", "delete", "danger")],
    ]
    if is_owner or is_admin:
        rows.insert(3, [btn("پنل مدیریت", "admin:panel", "admin", "success")])
    return InlineKeyboardMarkup(rows)


def profile_keyboard(user_id, own=False):
    rows = []
    if own:
        rows.append([
            btn("تنظیم عکس", f"profile:photo:{user_id}", "photo"),
            btn("تنظیم بیو", f"profile:bio:{user_id}", "profile"),
        ])
    rows.append([btn("نمایش سازنده: روشن/خاموش", f"profile:creator:{user_id}", "profile")])
    rows.append([
        btn("برگشت", "menu:home", "back"),
        btn("بستن", "menu:close", "delete", "danger"),
    ])
    return InlineKeyboardMarkup(rows)


async def profile_text(user_id):
    row = await db.fetchone(
        "SELECT username,first_name,bio,role,show_creator,character FROM users WHERE user_id=%s",
        (user_id,),
    )
    if not row:
        return None
    username, first, bio, role, show_creator, character = row
    edits = (await db.fetchone(
        "SELECT COUNT(*) FROM media WHERE sender_id=%s AND kind='video' AND approved=1",
        (user_id,),
    ))[0]
    musics = (await db.fetchone(
        "SELECT COUNT(*) FROM media WHERE sender_id=%s AND kind IN ('audio','document_audio') AND approved=1",
        (user_id,),
    ))[0]
    photos = (await db.fetchone(
        "SELECT COUNT(*) FROM media WHERE sender_id=%s AND kind='photo' AND approved=1",
        (user_id,),
    ))[0]
    likes = (await db.fetchone(
        "SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id "
        "WHERE m.sender_id=%s AND m.approved=1 AND v.vote=1",
        (user_id,),
    ))[0]
    dislikes = (await db.fetchone(
        "SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id "
        "WHERE m.sender_id=%s AND m.approved=1 AND v.vote=-1",
        (user_id,),
    ))[0]
    score = edits + musics + photos + likes
    level = max(1, score // 10 + 1)
    progress = score % 10
    remaining = 10 - progress if progress else 10
    bar = "█" * progress + "░" * (10 - progress)
    total_votes = likes + dislikes
    rating = (likes / total_votes * 10) if total_votes else None
    rating_text = f"{rating:.1f}/10" if rating is not None else "بدون رأی"
    ranked = []
    for (uid,) in await db.fetchall("SELECT user_id FROM users"):
        uploads = (await db.fetchone(
            "SELECT COUNT(*) FROM media WHERE sender_id=%s AND approved=1", (uid,)
        ))[0]
        user_likes = (await db.fetchone(
            "SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id "
            "WHERE m.sender_id=%s AND v.vote=1", (uid,),
        ))[0]
        ranked.append((uid, uploads + user_likes))
    ranked.sort(key=lambda item: item[1], reverse=True)
    rank = next((i for i, (uid, _) in enumerate(ranked, 1) if uid == user_id), len(ranked) + 1)
    role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
    name = ("@" + username) if username else (first or "کاربر")
    bio_line = f"\n\n<b>درباره من:</b> {safe(bio)}" if bio else ""
    return (
        f"{e('brand')} {e('brand2')} {e('brand3')} <b>پروفایل کاربر</b>\n\n"
        f"نام کاربری: <b>{safe(name)}</b>\nشناسه: <code>{user_id}</code>\n"
        f"مقام: {e(role_icon)} <b>{safe(role)}</b>\nسطح: <b>{level}</b>  ·  رتبه: <b>#{rank}</b>\n"
        f"کرکتر: <b>{safe(character or 'None')}</b>{bio_line}\n"
        f"پیشرفت لول: <code>{bar}</code>  <b>{progress}/10</b>\n"
        f"{remaining} امتیاز تا لول بعدی\n"
        f"نمایش سازنده زیر محتوا: <b>{'روشن' if show_creator else 'خاموش'}</b>\n\n"
        f"{e('edit')} ادیت: <b>{edits}</b>\n{e('music')} موزیک: <b>{musics}</b>\n"
        f"{e('photo')} عکس: <b>{photos}</b>\n"
        f"{e('like')} لایک: <b>{likes}</b>  {e('dislike')} دیسلایک: <b>{dislikes}</b>\n"
        f"{e('stats')} ریتینگ: <b>{rating_text}</b>"
    )


async def save_media(file_id, kind, user, approved):
    await ensure_user(user)
    media_id = await db.execute_returning(
        "INSERT INTO media(file_id,kind,sender_id,username,approved,created_at,review_status) "
        "VALUES(%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,%s) RETURNING id",
        (file_id, kind, user.id, user.username or user.first_name or "کاربر",
         approved, "approved" if approved else "pending"),
    )
    return media_id[0]


async def send_media(chat_id, file_id, kind, caption, markup, context):
    kwargs = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
        "reply_markup": markup,
    }
    if kind == "video":
        return await context.bot.send_video(video=file_id, **kwargs)
    if kind == "audio":
        return await context.bot.send_audio(audio=file_id, **kwargs)
    if kind == "document_audio":
        return await context.bot.send_document(document=file_id, **kwargs)
    if kind == "photo":
        return await context.bot.send_photo(photo=file_id, **kwargs)


async def is_privileged(user_id):
    if user_id == OWNER_ID:
        return True
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (user_id,))
    return bool(row and row[0] == "Admin")


async def command_rate_limited(user_id):
    if await is_privileged(user_id):
        return False
    return not await rate_limiter.allow(user_id, "command", 1, 2)


async def media_rate_limited(user_id):
    if await is_privileged(user_id):
        return False
    return not await rate_limiter.allow(user_id, "media", 10, 120)


async def get_review_group_id():
    row = await db.fetchone(
        "SELECT setting_value FROM bot_settings WHERE setting_key='review_group_id'"
    )
    return int(row[0]) if row else None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
async def set_review_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not user or user.id != OWNER_ID:
        await msg.reply_text("فقط Owner می‌تواند گروه تأیید را تنظیم کند.")
        return
    if not chat or chat.type not in ("group", "supergroup"):
        await msg.reply_text("این دستور را داخل گروه خصوصی مدیریت اجرا کن.")
        return
    await db.execute(
        "INSERT INTO bot_settings(setting_key,setting_value) VALUES('review_group_id',%s) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        (str(chat.id),),
    )
    await msg.reply_text(
        f"گروه تأیید i3l.exe تنظیم شد.\\nنام گروه: {safe(chat.title or 'گروه مدیریت')}\\nشناسه: <code>{chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def restore_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    if not user or user.id != OWNER_ID:
        await msg.reply_text("این دستور فقط برای Owner است.")
        return
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("شناسه محتوا را وارد کن: /restoremedia 123")
        return
    mid = int(context.args[0])
    rowcount = await db.execute(
        "UPDATE media SET approved=1, review_status='approved' "
        "WHERE id=%s AND review_status='rejected'",
        (mid,),
    )
    if not rowcount:
        await msg.reply_text("محتوای ردشده‌ای با این شناسه پیدا نشد.")
        return
    await db.execute(
        "INSERT INTO moderation_audit(media_id,action,moderator_id,moderator_name) VALUES(%s,%s,%s,%s)",
        (mid, "restored", user.id, user.username or user.full_name),
    )
    await msg.reply_text(f"محتوای شماره {mid} به آرشیو بازگردانده شد.")


async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.message, update.effective_user
    if msg.photo and await state.pop(user.id, "await_profile_photo", False):
        photo_id = msg.photo[-1].file_id
        await db.execute("UPDATE users SET profile_photo_id=%s WHERE user_id=%s", (photo_id, user.id))
        await msg.reply_text("عکس پروفایل ذخیره شد.")
        return
    if msg.video:
        file_id, kind = msg.video.file_id, "video"
    elif msg.audio:
        file_id, kind = msg.audio.file_id, "audio"
    elif msg.photo:
        file_id, kind = msg.photo[-1].file_id, "photo"
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio/"):
        file_id, kind = msg.document.file_id, "document_audio"
    else:
        return

    if await media_rate_limited(user.id):
        await msg.reply_text("محدودیت ضداسپم: حداکثر ۱۰ محتوا در هر ۲ دقیقه مجاز است. کمی صبر کن و دوباره بفرست.")
        return

    approved = 1 if user.id == OWNER_ID else 0
    media_id = await save_media(file_id, kind, user, approved)
    if approved:
        await msg.reply_text(f"{e('approve')} ذخیره شد و وارد آرشیو شد.", parse_mode=ParseMode.HTML)
        return

    review_group_id = await get_review_group_id()
    if not review_group_id:
        await msg.reply_text(
            "محتوایت ثبت شد، اما گروه تأیید هنوز توسط Owner تنظیم نشده است. فعلاً در انتظار بررسی می‌ماند."
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                "محتوای جدید شماره %d در انتظار بررسی است؛ گروه تأیید هنوز تنظیم نشده. دستور /setreviewgroup را داخل گروه مدیریت اجرا کن." % media_id,
            )
        except Exception as exc:
            logger.warning("Could not notify owner about unconfigured review group: %s", exc)
        return

    label = {"video": "ادیت", "photo": "عکس", "audio": "موزیک", "document_audio": "موزیک"}[kind]
    caption = (
        f"{e('upload')} <b>درخواست بررسی محتوا #{media_id}</b>\\n"
        f"فرستنده: {safe('@' + user.username if user.username else user.first_name)}\\n"
        f"شناسه کاربر: <code>{user.id}</code>\\nنوع: {label}"
    )
    try:
        sent_message = await send_media(
            review_group_id, file_id, kind, caption, review_buttons(media_id), context
        )
        await db.execute(
            "INSERT INTO review_messages(media_id,chat_id,message_id,caption) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT (media_id, chat_id, message_id) DO UPDATE SET caption=excluded.caption",
            (media_id, review_group_id, sent_message.message_id, caption),
        )
        await msg.reply_text(f"{e('upload')} محتوا برای بررسی در گروه مدیریت ارسال شد.", parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.exception("Could not send media %s to review group", media_id)
        await msg.reply_text(
            "محتوا ثبت شد، اما ارسال آن به گروه مدیریت ناموفق بود. به Owner اطلاع داده شد."
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"ارسال محتوای شماره {media_id} به گروه بررسی ناموفق بود. مطمئن شو بات عضو گروه است و اجازه ارسال مدیا دارد. خطا: {safe(exc)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Could not notify owner about review delivery failure")


async def review_callback(update, context):
    q = update.callback_query
    if not await is_privileged(q.from_user.id):
        await q.answer("فقط Owner و ادمین‌های ثبت‌شده اجازه بررسی دارند.", show_alert=True)
        return
    try:
        _, mid, decision = q.data.split(":")
        mid, decision = int(mid), int(decision)
        if decision not in (0, 1):
            raise ValueError
    except (ValueError, AttributeError):
        await q.answer("درخواست نامعتبر است.", show_alert=True)
        return

    reviewer_name = "@" + q.from_user.username if q.from_user.username else q.from_user.full_name
    action = "approved" if decision else "rejected"
    status_text = "تأیید شد" if decision else "رد شد"

    # Atomic state transition: only the first reviewer can decide.
    rowcount = await db.execute(
        "UPDATE media SET approved=%s, review_status=%s, reviewed_by=%s, reviewed_name=%s, "
        "reviewed_at=CURRENT_TIMESTAMP WHERE id=%s AND approved=0 AND review_status='pending'",
        (1 if decision else 0, action, q.from_user.id, reviewer_name, mid),
    )
    if not rowcount:
        await q.answer("این محتوا قبلاً بررسی شده یا جزو درخواست‌های قدیمی است.", show_alert=True)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await db.execute(
        "INSERT INTO moderation_audit(media_id,action,moderator_id,moderator_name) VALUES(%s,%s,%s,%s)",
        (mid, action, q.from_user.id, reviewer_name),
    )
    await q.answer("تصمیم ثبت شد.")

    handled_line = f"\\n\\n<b>{status_text}</b> توسط: {safe(reviewer_name)} · شناسه: <code>{q.from_user.id}</code>"
    try:
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                caption=(q.message.caption or "") + handled_line,
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        elif q.message and q.message.text is not None:
            await q.edit_message_text(
                text=q.message.text + handled_line,
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
    except Exception as exc:
        logger.warning("Could not update moderation message for media %s: %s", mid, exc)

    sender_row = await db.fetchone("SELECT sender_id FROM media WHERE id=%s", (mid,))
    if sender_row:
        sender_message = (
            f"{e('approve')} محتوای شما تأیید شد."
            if decision else f"{e('reject')} محتوای شما رد شد."
        )
        try:
            await context.bot.send_message(sender_row[0], sender_message, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.info("Could not notify sender about media %s: %s", mid, exc)

    await db.execute("DELETE FROM review_messages WHERE media_id=%s", (mid,))


async def send_random(update, context, kinds, label, key):
    items = await media_rows(kinds)
    if not items:
        await update.effective_message.reply_text(
            f"{e(key)} هنوز محتوایی در آرشیو نیست.", parse_mode=ParseMode.HTML
        )
        return
    mid, file_id, kind, sender, username = random.choice(items)
    row = await db.fetchone("SELECT role,show_creator FROM users WHERE user_id=%s", (sender,))
    role, show_creator = row if row else ("Member", 1)
    caption = f"{e(key)} <b>{label}</b>"
    if show_creator:
        uname = ("@" + username) if username and not username.startswith("@") else username
        role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
        caption += (
            f"\nسازنده: <a href=\"tg://user?id={sender}\">{safe(uname)}</a> "
            f"{e(role_icon)} <b>{safe(role)}</b>"
        )
    await send_media(update.effective_chat.id, file_id, kind, caption, await vote_buttons(mid), context)


async def random_video(update, context):
    user = update.effective_user
    if user and await command_rate_limited(user.id):
        await update.effective_message.reply_text("برای جلوگیری از اسپم، بین دستورها ۲ ثانیه فاصله بگذار.")
        return
    await send_random(update, context, ("video",), "ادیت", "edit")


async def random_music(update, context):
    user = update.effective_user
    if user and await command_rate_limited(user.id):
        await update.effective_message.reply_text("برای جلوگیری از اسپم، بین دستورها ۲ ثانیه فاصله بگذار.")
        return
    await send_random(update, context, ("audio", "document_audio"), "موزیک", "music")


async def random_photo(update, context):
    user = update.effective_user
    if user and await command_rate_limited(user.id):
        await update.effective_message.reply_text("برای جلوگیری از اسپم، بین دستورها ۲ ثانیه فاصله بگذار.")
        return
    await send_random(update, context, ("photo",), "عکس", "photo")


async def vote_callback(update, context):
    q = update.callback_query
    _, mid, vote = q.data.split(":")
    mid, vote = int(mid), int(vote)
    item = await db.fetchone("SELECT sender_id FROM media WHERE id=%s AND approved=1", (mid,))
    if not item:
        await q.answer("محتوا در دسترس نیست.")
        return
    if item[0] == q.from_user.id and q.from_user.id != OWNER_ID:
        await q.answer("نمی‌توانی به محتوای خودت رأی بدهی.", show_alert=True)
        return
    await db.execute(
        "INSERT INTO votes(media_id,voter_id,vote) VALUES(%s,%s,%s) "
        "ON CONFLICT(media_id,voter_id) DO UPDATE SET vote=excluded.vote",
        (mid, q.from_user.id, vote),
    )
    await q.answer("رأی ثبت شد.")
    await q.edit_message_reply_markup(reply_markup=await vote_buttons(mid))


async def ranking_text(kind):
    kinds = {
        "edit": ("video",),
        "music": ("audio", "document_audio"),
        "photo": ("photo",),
        "all": ("video", "audio", "document_audio", "photo"),
    }[kind]
    marks = ",".join("%s" for _ in kinds)
    rows = await db.fetchall(
        f"SELECT sender_id,MAX(username),COUNT(*) FROM media "
        f"WHERE kind IN ({marks}) AND approved=1 GROUP BY sender_id", kinds,
    )
    stats = []
    for uid, username, count in rows:
        mids = [r[0] for r in await db.fetchall(
            f"SELECT id FROM media WHERE sender_id=%s AND kind IN ({marks}) AND approved=1",
            (uid, *kinds),
        )]
        likes = dislikes = 0
        for mid in mids:
            up, down = await vote_counts(mid)
            likes += up
            dislikes += down
        stats.append((uid, username or str(uid), count, likes, dislikes))
    stats.sort(key=lambda item: (item[3], item[2]), reverse=True)
    title = {
        "edit": "رتبه‌بندی ادیتورها",
        "music": "رتبه‌بندی موزیک‌فرست‌ها",
        "photo": "رتبه‌بندی عکاس‌ها",
        "all": "رتبه‌بندی کلی",
    }[kind]
    if not stats:
        return f"{e('rank')} <b>{title}</b>\n\nهنوز داده‌ای ثبت نشده."
    out = f"{e('rank')} <b>{title}</b>\n\n"
    for place, (uid, name, count, likes, dislikes) in enumerate(stats[:10], 1):
        shown_name = name if str(name).startswith("@") else "@" + str(name)
        out += (
            f"<b>#{place}</b> <a href=\"tg://user?id={uid}\">{safe(shown_name)}</a>\n"
            f"محتوا: {count} | {e('like')} {likes} | {e('dislike')} {dislikes}\n\n"
        )
    return out


async def top_users_text():
    rows = await db.fetchall(
        "SELECT sender_id,MAX(username),COUNT(*) FROM media WHERE approved=1 GROUP BY sender_id"
    )
    scored = []
    for uid, username, uploads in rows:
        likes = (await db.fetchone(
            "SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id "
            "WHERE m.sender_id=%s AND v.vote=1", (uid,),
        ))[0]
        scored.append((uid, username or str(uid), uploads, likes, uploads + likes))
    scored.sort(key=lambda item: (item[4], item[3], item[2]), reverse=True)
    out = f"{e('brand')} {e('rank')} <b>برترین کاربران i3l.exe</b>\n\n"
    if not scored:
        return out + "هنوز فعالیتی برای رتبه‌بندی ثبت نشده."
    for place, (uid, username, uploads, likes, score) in enumerate(scored[:3], 1):
        name = username if str(username).startswith("@") else "@" + str(username)
        role_row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (uid,))
        role = role_row[0] if role_row else "Member"
        role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
        out += (
            f"<b>#{place}</b> <a href=\"tg://user?id={uid}\">{safe(name)}</a> {e(role_icon)}\n"
            f"محتوا: <b>{uploads}</b> · لایک: <b>{likes}</b> · امتیاز: <b>{score}</b>\n\n"
        )
    return out


async def top_media_text(kind):
    kinds = {
        "video": ("video", "ویدیوهای برتر", "edit"),
        "photo": ("photo", "عکس‌های برتر", "photo"),
        "music": ("audio", "موزیک‌های برتر", "music"),
    }
    db_kind, title, icon = kinds[kind]
    if kind == "music":
        rows = await db.fetchall(
            "SELECT id,file_id,kind,sender_id,username FROM media "
            "WHERE kind IN ('audio','document_audio') AND approved=1"
        )
    else:
        rows = await db.fetchall(
            "SELECT id,file_id,kind,sender_id,username FROM media WHERE kind=%s AND approved=1",
            (db_kind,),
        )
    scored = []
    for item in rows:
        mid = item[0]
        likes, dislikes = await vote_counts(mid)
        scored.append((likes - dislikes, likes, item))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    out = f"{e(icon)} <b>{title} | منتخب i3l.exe</b>\n\n"
    if not scored:
        return out + "هنوز محتوایی در این بخش ثبت نشده."
    for place, (_, likes, item) in enumerate(scored[:3], 1):
        mid, _, _, sender, username = item
        name = ("@" + username) if username and not username.startswith("@") else (username or str(sender))
        out += f"<b>#{place}</b> {safe(name)} · {e('like')} {likes} · شناسه محتوا: <code>{mid}</code>\n"
    return out


def rank_menu():
    return InlineKeyboardMarkup([
        [btn("رنک ادیت", "rank:edit", "edit"), btn("رنک موزیک", "rank:music", "music")],
        [btn("رنک عکس", "rank:photo", "photo"), btn("رنک کلی", "rank:all", "rank")],
        [btn("برترین کاربران", "rank:tops", "owner", "success")],
        [btn("برترین محتوا", "rank:content", "approve", "success")],
        [btn("برگشت", "menu:home", "back"), btn("بستن", "menu:close", "delete", "danger")],
    ])


def top_content_menu():
    return InlineKeyboardMarkup([
        [btn("۳ ویدیوی برتر", "top:video", "edit")],
        [btn("۳ عکس برتر", "top:photo", "photo")],
        [btn("۳ موزیک برتر", "top:music", "music")],
        [btn("برگشت", "menu:ranks", "back"), btn("بستن", "menu:close", "delete", "danger")],
    ])


async def ranking(update, context):
    user = update.effective_user
    if user and await command_rate_limited(user.id):
        await update.effective_message.reply_text("برای جلوگیری از اسپم، بین دستورها ۲ ثانیه فاصله بگذار.")
        return
    await update.effective_message.reply_text(
        f"{e('rank')} <b>مرکز رتبه‌بندی i3l.exe</b>\nبخش موردنظرت رو انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=rank_menu(),
    )


async def profile_command(update, context):
    target = update.effective_user
    if update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user:
        target = update.effective_message.reply_to_message.from_user
    await ensure_user(target)
    own = target.id == update.effective_user.id
    photo_row = await db.fetchone("SELECT profile_photo_id FROM users WHERE user_id=%s", (target.id,))
    photo = photo_row[0] if photo_row else None
    if photo:
        await update.effective_message.reply_photo(
            photo, caption=await profile_text(target.id), parse_mode=ParseMode.HTML,
            reply_markup=profile_keyboard(target.id, own),
        )
    else:
        await update.effective_message.reply_text(
            await profile_text(target.id), parse_mode=ParseMode.HTML,
            reply_markup=profile_keyboard(target.id, own),
        )


async def start(update, context):
    await ensure_user(update.effective_user)
    txt = (
        f"{e('brand')} {e('brand2')} {e('brand3')} <b>به i3l.exe خوش اومدی</b>\n\n"
        "اینجا آرشیو ادیت، موزیک و عکس همراه با پروفایل و سیستم رتبه‌بندی در دسترسه. "
        "از منوی لمسی زیر بخش موردنظرت رو انتخاب کن. برای ثبت محتوا، فایل رو در گفت‌وگوی خصوصی بات بفرست تا بررسی بشه."
    )
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (update.effective_user.id,))
    await update.effective_message.reply_text(
        txt, parse_mode=ParseMode.HTML,
        reply_markup=main_menu(
            update.effective_user.id == OWNER_ID,
            bool(row and row[0] == "Admin"),
        ),
    )


def help_text():
    return (
        f"{e('brand')} {e('help')} <b>راهنمای رسمی i3l.exe</b>\n\n"
        f"{e('edit')} <b>ادیت</b> — دریافت ادیت تصادفی از آرشیو تأییدشده\n"
        f"{e('music')} <b>موزیک</b> — دریافت موزیک تصادفی\n"
        f"{e('photo')} <b>عکس</b> — دریافت عکس تصادفی\n"
        f"{e('profile')} <b>پروفایل</b> — مشاهده پروفایل؛ برای دیدن پروفایل دیگران روی پیامشان ریپلای کن\n"
        f"{e('rank')} <b>رنک</b> — رتبه‌بندی کاربران و برترین محتوا\n"
        f"{e('upload')} <b>ثبت محتوا</b> — فایل ویدیویی، عکس یا موزیک را در پیوی بات بفرست؛ پس از بررسی مدیریت به آرشیو اضافه می‌شود\n"
        f"{e('like')} <b>امتیازدهی</b> — برای هر محتوا یک رأی ثبت می‌شود و امکان تغییر رأی وجود دارد\n"
        f"{e('stats')} <b>سیستم سطح</b> — با ارسال محتوای تأییدشده و دریافت لایک، امتیاز فعالیت افزایش پیدا می‌کند."
    )


async def help_command(update, context):
    await ensure_user(update.effective_user)
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (update.effective_user.id,))
    await update.effective_message.reply_text(
        help_text(), parse_mode=ParseMode.HTML,
        reply_markup=main_menu(
            update.effective_user.id == OWNER_ID,
            bool(row and row[0] == "Admin"),
        ),
    )


async def edit_callback_message(q, text, reply_markup=None):
    msg = q.message
    if msg and (msg.photo or msg.video or msg.audio or msg.document):
        return await q.edit_message_caption(
            caption=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    return await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
    )


async def menu_callback(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "menu:close":
        await edit_callback_message(q, f"{e('brand')} <b>پنل بسته شد.</b>", None)
    elif data == "menu:home":
        row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (q.from_user.id,))
        await edit_callback_message(
            q, f"{e('brand')} {e('brand2')} {e('brand3')} <b>منوی i3l.exe</b>",
            main_menu(q.from_user.id == OWNER_ID, bool(row and row[0] == "Admin")),
        )
    elif data == "menu:profile":
        await ensure_user(q.from_user)
        row = await db.fetchone("SELECT profile_photo_id FROM users WHERE user_id=%s", (q.from_user.id,))
        photo = row[0] if row else None
        if photo:
            await q.message.reply_photo(
                photo, caption=await profile_text(q.from_user.id), parse_mode=ParseMode.HTML,
                reply_markup=profile_keyboard(q.from_user.id, True),
            )
        else:
            await edit_callback_message(
                q, await profile_text(q.from_user.id), profile_keyboard(q.from_user.id, True)
            )
    elif data in ("menu:edit", "menu:music", "menu:photo"):
        await q.edit_message_text(
            "برای دریافت محتوا، دستور مربوطه را در گروهی که بات عضو آن است ارسال کن: ادیت، موزیک یا عکس.",
            reply_markup=InlineKeyboardMarkup([[btn("برگشت", "menu:home", "back")]]),
        )
    elif data == "menu:stats":
        total = (await db.fetchone("SELECT COUNT(*) FROM media WHERE approved=1"))[0]
        edits = (await db.fetchone("SELECT COUNT(*) FROM media WHERE approved=1 AND kind='video'"))[0]
        music = (await db.fetchone("SELECT COUNT(*) FROM media WHERE approved=1 AND kind IN ('audio','document_audio')"))[0]
        photos = (await db.fetchone("SELECT COUNT(*) FROM media WHERE approved=1 AND kind='photo'"))[0]
        people = (await db.fetchone("SELECT COUNT(*) FROM users"))[0]
        await q.edit_message_text(
            f"{e('stats')} <b>آمار i3l.exe</b>\n\nکاربران: {people}\nکل محتوا: {total}\nادیت: {edits}\nموزیک: {music}\nعکس: {photos}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("برگشت", "menu:home", "back")]]),
        )
    elif data == "menu:help":
        await q.edit_message_text(
            help_text(), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("برگشت", "menu:home", "back")]]),
        )
    elif data == "menu:ranks":
        await q.edit_message_text(
            f"{e('rank')} <b>مرکز رتبه‌بندی i3l.exe</b>\nبخش موردنظرت رو انتخاب کن:",
            parse_mode=ParseMode.HTML, reply_markup=rank_menu(),
        )
    elif data.startswith("rank:"):
        kind = data.split(":", 1)[1]
        if kind == "content":
            await q.edit_message_text(
                f"{e('approve')} <b>برترین محتوای i3l.exe</b>\nنوع محتوا رو انتخاب کن:",
                parse_mode=ParseMode.HTML, reply_markup=top_content_menu(),
            )
        else:
            result = await top_users_text() if kind == "tops" else await ranking_text(kind)
            await q.edit_message_text(
                result, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn("برگشت", "menu:ranks", "back")]]),
            )
    elif data.startswith("top:"):
        kind = data.split(":", 1)[1]
        await q.edit_message_text(
            await top_media_text(kind), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("برگشت", "rank:content", "back")]]),
        )
        if kind == "music":
            items = await db.fetchall(
                "SELECT id,file_id,kind,sender_id,username FROM media "
                "WHERE kind IN ('audio','document_audio') AND approved=1"
            )
        else:
            media_kind = "video" if kind == "video" else "photo"
            items = await db.fetchall(
                "SELECT id,file_id,kind,sender_id,username FROM media "
                "WHERE kind=%s AND approved=1", (media_kind,),
            )
        ranked_items = []
        for item in items:
            likes, dislikes = await vote_counts(item[0])
            ranked_items.append((likes - dislikes, likes, item))
        ranked_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for place, (_, likes, item) in enumerate(ranked_items[:3], 1):
            mid, file_id, media_kind, sender, username = item
            label = {"video": "ویدیوی برتر", "photo": "عکس برتر", "music": "موزیک برتر"}[kind]
            caption = (
                f"{e('brand')} <b>{label} #{place}</b>\n"
                f"{e('like')} لایک: <b>{likes}</b>\n"
                f"منتخب i3l.exe"
            )
            await send_media(q.message.chat_id, file_id, media_kind, caption, await vote_buttons(mid), context)
    elif data.startswith("profile:"):
        _, action, uid = data.split(":")
        uid = int(uid)
        if uid != q.from_user.id:
            await q.answer("این تنظیمات فقط برای صاحب پروفایل است.", show_alert=True)
            return
        if action == "colors":
            kb = InlineKeyboardMarkup([
                [btn("سبز", "color:green", "approve", "success"),
                 btn("آبی", "color:blue", "profile"),
                 btn("قرمز", "color:red", "reject", "danger")],
                [btn("برگشت", "menu:profile", "back")],
            ])
            await q.edit_message_text("رنگ پروفایل را انتخاب کن:", reply_markup=kb)
        elif action == "creator":
            await db.execute(
                "UPDATE users SET show_creator=CASE WHEN show_creator=1 THEN 0 ELSE 1 END WHERE user_id=%s",
                (uid,),
            )
            await edit_callback_message(q, await profile_text(uid), profile_keyboard(uid, True))
        elif action == "bio":
            await state.set(q.from_user.id, "await_bio", True)
            await edit_callback_message(
                q, "بیوی جدیدت را به صورت یک پیام متنی بفرست.",
                InlineKeyboardMarkup([[btn("برگشت", "menu:home", "back")]]),
            )
        elif action == "photo":
            await state.set(q.from_user.id, "await_profile_photo", True)
            await edit_callback_message(
                q, "عکس موردنظرت را همین‌جا ارسال کن.",
                InlineKeyboardMarkup([[btn("برگشت", "menu:home", "back")]]),
            )
    elif data.startswith("color:"):
        color = data.split(":", 1)[1]
        if q.from_user.id == OWNER_ID:
            await db.execute("UPDATE users SET role='Owner',color='Owner' WHERE user_id=%s", (OWNER_ID,))
        else:
            await db.execute("UPDATE users SET color=%s WHERE user_id=%s", (color, q.from_user.id))
        await edit_callback_message(
            q, await profile_text(q.from_user.id), profile_keyboard(q.from_user.id, True)
        )


async def text_router(update, context):
    msg, user = update.effective_message, update.effective_user
    if not user or not msg or not msg.text:
        return
    await ensure_user(user)
    if await state.pop(user.id, "await_bio", False):
        await db.execute("UPDATE users SET bio=%s WHERE user_id=%s", (msg.text[:160], user.id))
        await msg.reply_text("بیو ذخیره شد.")
        return
    if await state.pop(user.id, "await_media_delete", False):
        row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (user.id,))
        if user.id != OWNER_ID and (not row or row[0] != "Admin"):
            await msg.reply_text("اجازه حذف محتوا نداری.")
            return
        raw_mid = msg.text.strip()
        if not raw_mid.isdigit():
            await msg.reply_text("شناسه محتوا باید عددی باشد. دوباره شناسه را بفرست.")
            await state.set(user.id, "await_media_delete", True)
            return
        mid = int(raw_mid)
        exists = await db.fetchone("SELECT id FROM media WHERE id=%s", (mid,))
        if not exists:
            await msg.reply_text("محتوایی با این شناسه پیدا نشد.")
            return
        await db.execute("DELETE FROM votes WHERE media_id=%s", (mid,))
        await db.execute("DELETE FROM media WHERE id=%s", (mid,))
        await msg.reply_text(
            f"محتوای شماره <code>{mid}</code> از آرشیو حذف شد.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard(user.id == OWNER_ID),
        )
        return
    pending_role = await state.get(user.id, "await_role_user")
    if pending_role:
        if user.id != OWNER_ID:
            await state.pop(user.id, "await_role_user", None)
            return
        raw_id = msg.text.strip()
        if raw_id.startswith("@"):
            username = raw_id[1:].strip().lower()
            found = await db.fetchone(
                "SELECT user_id FROM users WHERE lower(username)=%s", (username,)
            )
            if not found:
                await msg.reply_text(
                    "این یوزرنیم در کاربران بات پیدا نشد. کاربر باید یک بار وارد پیوی بات شود و /start بزند؛ بعد دوباره یوزرنیمش را بفرست."
                )
                return
            target_id = found[0]
        elif raw_id.isdigit():
            target_id = int(raw_id)
        else:
            await msg.reply_text("آیدی عددی یا یوزرنیم با @ بفرست.")
            return
        role = pending_role["role"]
        action = pending_role["action"]
        await state.pop(user.id, "await_role_user", None)
        if target_id == OWNER_ID:
            await msg.reply_text("مقام سازنده قابل تغییر نیست.")
            return
        if action == "add":
            await db.execute(
                "INSERT INTO users(user_id,role) VALUES(%s,%s) "
                "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role",
                (target_id, role),
            )
            result = f"کاربر <code>{target_id}</code> با مقام <b>{role}</b> ثبت شد."
        else:
            await db.execute(
                "UPDATE users SET role='Member' WHERE user_id=%s AND role=%s",
                (target_id, role),
            )
            result = f"مقام <b>{role}</b> از کاربر <code>{target_id}</code> برداشته شد."
        await msg.reply_text(
            result, parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard(user.id == OWNER_ID),
        )
        return
    if msg.text.strip() in ("پروفایل", "پروفایل من"):
        await profile_command(update, context)


async def count_group_messages(update, context):
    msg = update.effective_message
    if not msg or msg.chat.type not in ("group", "supergroup"):
        return
    await db.execute(
        "INSERT INTO group_chats(chat_id,title,chat_type,active) VALUES(%s,%s,%s,1) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,chat_type=excluded.chat_type,active=1",
        (msg.chat_id, msg.chat.title or "گروه", msg.chat.type),
    )
    row = await db.fetchone("SELECT message_count FROM group_counts WHERE chat_id=%s", (msg.chat_id,))
    count = (row[0] if row else 0) + 1
    await db.execute(
        "INSERT INTO group_counts(chat_id,message_count) VALUES(%s,%s) "
        "ON CONFLICT(chat_id) DO UPDATE SET message_count=excluded.message_count",
        (msg.chat_id, count),
    )
    if count % AUTO_EDIT_EVERY != 0:
        return
    # The automatic feature spotlights the bot, not the uploader.
    intro = (
        f"{e('brand')} {e('brand2')} {e('brand3')} <b>منتخب i3l.exe</b>\n\n"
        f"{e('edit')} <b>ادیت</b> — دریافت ادیت تصادفی\n"
        f"{e('music')} <b>موزیک</b> — دریافت موزیک تصادفی\n"
        f"{e('photo')} <b>عکس</b> — دریافت عکس تصادفی\n"
        f"{e('profile')} <b>پروفایل</b> — مشاهده پروفایل\n"
        f"{e('rank')} <b>رنک</b> — رتبه‌بندی کاربران و برترین محتوا\n\n"
        "می‌توانی با فرستادن محتوای خودت در پیوی بات، اون‌ها رو به اشتراک بذاری."
    )
    items = await media_rows(("video",))
    markup = InlineKeyboardMarkup([[btn("برترین محتوا", "rank:content", "approve", "success")]])
    if items:
        mid, file_id, kind, sender, username = random.choice(items)
        await send_media(msg.chat_id, file_id, kind, intro, await vote_buttons(mid), context)
        # Attach a separate discover button so the top-content area is accessible from the promo.
        try:
            await context.bot.send_message(
                msg.chat_id, f"{e('brand')} برای دیدن برترین محتوا از این بخش استفاده کن.",
                parse_mode=ParseMode.HTML, reply_markup=markup,
            )
        except Exception:
            logger.exception("Could not send top-content shortcut")
    else:
        await context.bot.send_message(
            msg.chat_id, intro, parse_mode=ParseMode.HTML, reply_markup=markup
        )


async def announce_command(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("این دستور فقط در اختیار سازنده بات است.")
        return
    msg = update.effective_message
    raw = msg.text or ""
    command_match = re.match(r"^/announce(?:@\w+)?", raw)
    if not command_match:
        await msg.reply_text("متن اعلامیه را بعد از دستور بنویس.")
        return
    body_start = command_match.end()
    if body_start < len(raw) and raw[body_start].isspace():
        body_start += 1  # remove only the command separator; preserve all other spacing
    announcement = raw[body_start:]
    if not announcement.strip():
        await msg.reply_text("متن اعلامیه را بعد از دستور بنویس. قالب‌بندی و ایموجی پریمیومی حفظ می‌شود.")
        return
    chats = await db.fetchall("SELECT chat_id,title FROM group_chats WHERE active=1")
    if not chats:
        await msg.reply_text("هنوز گروهی در فهرست بات ثبت نشده است.")
        return
    prefix = "اعلامیه رسمی i3l.exe\n\n"
    body_offset = len(raw[:body_start].encode("utf-16-le")) // 2
    prefix_offset = len(prefix.encode("utf-16-le")) // 2
    entities = []
    for entity in (msg.entities or []):
        if entity.type == "bot_command" or entity.offset + entity.length <= body_offset:
            continue
        cloned = copy.deepcopy(entity)
        original_end = cloned.offset + cloned.length
        cloned.offset = prefix_offset + max(0, cloned.offset - body_offset)
        cloned.length = max(0, original_end - max(body_offset, entity.offset))
        if cloned.length:
            entities.append(cloned)
    outgoing = prefix + announcement
    sent = failed = 0
    for chat_id, title in chats:
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=outgoing, entities=entities or None,
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Broadcast failed for %s (%s): %s", title, chat_id, exc)
            error_text = str(exc).lower()
            if "chat not found" in error_text or "bot was kicked" in error_text:
                await db.execute("UPDATE group_chats SET active=0 WHERE chat_id=%s", (chat_id,))
    await msg.reply_text(f"اعلامیه ارسال شد.\nموفق: {sent} گروه\nناموفق: {failed} گروه")


def admin_panel_keyboard(is_owner):
    rows = []
    if is_owner:
        rows.extend([
            [btn("افزودن Admin", "role:add:Admin", "admin", "success"),
             btn("حذف Admin", "role:remove:Admin", "delete", "danger")],
            [btn("افزودن VIP", "role:add:VIP", "vip", "success"),
             btn("حذف VIP", "role:remove:VIP", "delete", "danger")],
        ])
    rows.append([btn("حذف محتوا از آرشیو", "media:delete", "delete", "danger")])
    rows.append([btn("برگشت", "menu:home", "back")])
    return InlineKeyboardMarkup(rows)


async def admin_panel_callback(update, context):
    q = update.callback_query
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (q.from_user.id,))
    is_owner = q.from_user.id == OWNER_ID
    if not is_owner and (not row or row[0] != "Admin"):
        await q.answer("فقط سازنده و ادمین‌ها به این بخش دسترسی دارند.", show_alert=True)
        return
    await q.answer()
    await edit_callback_message(
        q,
        f"{e('admin')} <b>پنل مدیریت i3l.exe</b>\n\nعملیات موردنظرت رو انتخاب کن.",
        admin_panel_keyboard(is_owner),
    )


async def role_action_callback(update, context):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("فقط سازنده به این بخش دسترسی دارد.", show_alert=True)
        return
    _, action, role = q.data.split(":")
    await state.set(q.from_user.id, "await_role_user", {"action": action, "role": role})
    await q.answer()
    await edit_callback_message(
        q,
        f"یوزرنیم با @ یا آیدی عددی کاربر را بفرست تا {'به' if action == 'add' else 'از او'} مقام <b>{role}</b> {'داده شود' if action == 'add' else 'گرفته شود'}.",
        InlineKeyboardMarkup([[btn("لغو", "admin:panel", "back")]]),
    )


async def media_delete_callback(update, context):
    q = update.callback_query
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (q.from_user.id,))
    if q.from_user.id != OWNER_ID and (not row or row[0] != "Admin"):
        await q.answer("اجازه حذف محتوا نداری.", show_alert=True)
        return
    await state.set(q.from_user.id, "await_media_delete", True)
    await q.answer()
    await edit_callback_message(
        q,
        "شناسه عددی محتوایی که می‌خواهی از آرشیو حذف کنی بفرست. شناسه محتوا در بخش برترین محتوا هم نمایش داده می‌شود.",
        InlineKeyboardMarkup([[btn("لغو", "admin:panel", "back")]]),
    )


async def welcome(update, context):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await db.execute(
                "INSERT INTO group_chats(chat_id,title,chat_type,active) VALUES(%s,%s,%s,1) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,active=1",
                (update.effective_chat.id, update.effective_chat.title or "گروه", update.effective_chat.type),
            )
            await update.message.reply_text(
                f"{e('brand')} <b>شما بات i3l.exe رو اضافه کردین</b>\n\n"
                "ادیت، موزیک، عکس و رنک در دسترسه. برای راهنما /help رو بزنید.",
                parse_mode=ParseMode.HTML, reply_markup=main_menu(),
            )
            break


async def admin_panel_command(update, context):
    user = update.effective_user
    row = await db.fetchone("SELECT role FROM users WHERE user_id=%s", (user.id,))
    is_owner = user.id == OWNER_ID
    if not is_owner and (not row or row[0] != "Admin"):
        await update.effective_message.reply_text("این دستور فقط برای سازنده و ادمین‌های بات است.")
        return
    await update.effective_message.reply_text(
        f"{e('admin')} <b>پنل مدیریت i3l.exe</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard(is_owner),
    )
