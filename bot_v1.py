import os
import random
import sqlite3
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters)

OWNER_ID = 6341879634
BOT_NAME = "i3l.exe"
DB_NAME = "i3l.db"
AUTO_EDIT_EVERY = 100
logging.basicConfig(level=logging.INFO)

# Premium custom emoji IDs supplied by the bot owner.
EMOJI = {
    "brand": 4956770345555068016, "brand2": 4956358131773867289,
    "brand3": 4956698533701878973, "profile": 4938653911507534983,
    "admin": 5406711411541823609, "vip": 5253912783140960304,
    "owner": 5323261373801571717, "edit": 5924623057997007830,
    "music": 5190785418248876370, "photo": 5375074927252621134,
    "rank": 5409008750893734809, "help": 5226512880362332956,
    "like": 5255965021594203933, "dislike": 5377627447791466788,
    "approve": 5904704361182798355, "reject": 5161208387957950108,
    "stats": 5203993413346680064, "upload": 5318974936310627698,
    "delete": 6050913161131859293, "back": 6050826230993787980,
}

def e(key):
    eid = EMOJI.get(key)
    return f'<tg-emoji emoji-id="{eid}">⭐</tg-emoji>' if eid else ""

def safe(value):
    return html.escape(str(value or "—"))

def btn(text, callback, icon="brand", style="primary"):
    """Telegram Bot API 9.4+: colored inline button with a premium custom-emoji icon."""
    kwargs = {"text": text, "callback_data": callback, "style": style}
    if EMOJI.get(icon):
        kwargs["icon_custom_emoji_id"] = str(EMOJI[icon])
    return InlineKeyboardButton(**kwargs)

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.execute("PRAGMA foreign_keys=ON")
db.executescript("""
CREATE TABLE IF NOT EXISTS media (
 id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL, kind TEXT NOT NULL,
 sender_id INTEGER NOT NULL, username TEXT, approved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS votes (
 media_id INTEGER, voter_id INTEGER, vote INTEGER, UNIQUE(media_id, voter_id));
CREATE TABLE IF NOT EXISTS users (
 user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, bio TEXT DEFAULT '',
 color TEXT DEFAULT 'blue', role TEXT DEFAULT 'Member', profile_photo_id TEXT, joined_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS group_counts (
 chat_id INTEGER PRIMARY KEY, message_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS group_chats (
 chat_id INTEGER PRIMARY KEY, title TEXT, chat_type TEXT, active INTEGER DEFAULT 1,
 added_at TEXT DEFAULT CURRENT_TIMESTAMP);
""")
# Add missing columns safely for older databases.
cols = {r[1] for r in db.execute("PRAGMA table_info(media)")}
if "created_at" not in cols:
    db.execute("ALTER TABLE media ADD COLUMN created_at TEXT")
user_cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
if "profile_photo_id" not in user_cols:
    db.execute("ALTER TABLE users ADD COLUMN profile_photo_id TEXT")
db.commit()

def ensure_user(user):
    role = "Owner" if user.id == OWNER_ID else "Member"
    db.execute("INSERT INTO users(user_id,username,first_name,role) VALUES(?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
               (user.id, user.username, user.first_name, role))
    db.commit()

def media_rows(kinds, approved=1):
    marks = ",".join("?" for _ in kinds)
    return db.execute(f"SELECT id,file_id,kind,sender_id,username FROM media WHERE kind IN ({marks}) AND approved=?", (*kinds, approved)).fetchall()

def vote_counts(media_id):
    row = db.execute("SELECT SUM(CASE WHEN vote=1 THEN 1 ELSE 0 END), SUM(CASE WHEN vote=-1 THEN 1 ELSE 0 END) FROM votes WHERE media_id=?", (media_id,)).fetchone()
    return row[0] or 0, row[1] or 0

def vote_buttons(media_id):
    up, down = vote_counts(media_id)
    return InlineKeyboardMarkup([[
        btn(str(up), f"vote:{media_id}:1", "like", "success"),
        btn(str(down), f"vote:{media_id}:-1", "dislike", "danger")
    ]])

def review_buttons(media_id):
    return InlineKeyboardMarkup([[
        btn("تأیید", f"review:{media_id}:1", "approve", "success"),
        btn("رد", f"review:{media_id}:0", "reject", "danger")
    ]])

def main_menu():
    return InlineKeyboardMarkup([
      [btn("پروفایل من", "menu:profile", "profile"), btn("ادیت", "menu:edit", "edit")],
      [btn("موزیک", "menu:music", "music"), btn("عکس", "menu:photo", "photo")],
      [btn("رنک", "menu:ranks", "rank"), btn("برترین‌ها", "menu:tops", "owner", "success")],
      [btn("آمار", "menu:stats", "stats"), btn("راهنما", "menu:help", "help")]
    ])

def profile_keyboard(user_id, own=False):
    rows = []
    if own:
        rows.append([btn("تنظیم عکس", f"profile:photo:{user_id}", "photo", "primary"),
                     btn("تنظیم بیو", f"profile:bio:{user_id}", "profile", "primary")])
    rows.append([btn("انتخاب رنگ", f"profile:colors:{user_id}", "rank", "success")])
    rows.append([btn("بستن", "menu:close", "back", "danger")])
    return InlineKeyboardMarkup(rows)

def profile_text(user_id):
    row = db.execute("SELECT username,first_name,bio,color,role FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    username, first, bio, color, role = row
    edits = db.execute("SELECT COUNT(*) FROM media WHERE sender_id=? AND kind='video' AND approved=1", (user_id,)).fetchone()[0]
    musics = db.execute("SELECT COUNT(*) FROM media WHERE sender_id=? AND kind IN ('audio','document_audio') AND approved=1", (user_id,)).fetchone()[0]
    photos = db.execute("SELECT COUNT(*) FROM media WHERE sender_id=? AND kind='photo' AND approved=1", (user_id,)).fetchone()[0]
    votes = db.execute("SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id WHERE m.sender_id=? AND v.vote=1", (user_id,)).fetchone()[0]
    dislikes = db.execute("SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id WHERE m.sender_id=? AND v.vote=-1", (user_id,)).fetchone()[0]
    # Level and leaderboard score reward approved uploads and likes; dislikes are statistics only.
    score = edits + musics + photos + votes
    level = max(1, score // 10 + 1)
    all_users = db.execute("SELECT user_id FROM users").fetchall()
    ranked = []
    for (uid,) in all_users:
        media_count = db.execute("SELECT COUNT(*) FROM media WHERE sender_id=? AND approved=1", (uid,)).fetchone()[0]
        likes = db.execute("SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id WHERE m.sender_id=? AND v.vote=1", (uid,)).fetchone()[0]
        ranked.append((uid, media_count + likes))
    ranked.sort(key=lambda x: x[1], reverse=True)
    rank = next((i for i,(uid,_) in enumerate(ranked,1) if uid==user_id), len(ranked)+1)
    role_emoji = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
    name = ("@" + username) if username else (first or "کاربر")
    bio_line = f"\n\n<b>درباره من:</b> {safe(bio)}" if bio else ""
    return (f"{e('brand')} {e('brand2')} {e('brand3')} <b>پروفایل کاربر</b>\n\n"
      f"نام کاربری: <b>{safe(name)}</b>\nشناسه: <code>{user_id}</code>\n"
      f"مقام: {e(role_emoji)} <b>{safe(role)}</b>\nسطح: <b>{level}</b>  ·  رتبه: <b>#{rank}</b>\n"
      f"رنگ حساب: <b>{safe(color)}</b>{bio_line}\n\n"
      f"{e('edit')} ادیت: <b>{edits}</b>\n{e('music')} موزیک: <b>{musics}</b>\n{e('photo')} عکس: <b>{photos}</b>\n"
      f"{e('like')} لایک: <b>{votes}</b>  {e('dislike')} دیسلایک: <b>{dislikes}</b>\n"
      f"{e('stats')} امتیاز فعالیت: <b>{score}</b>")

def save_media(file_id, kind, user, approved):
    ensure_user(user)
    cur = db.execute("INSERT INTO media(file_id,kind,sender_id,username,approved,created_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)",
                     (file_id, kind, user.id, user.username or user.first_name or "کاربر", approved))
    db.commit(); return cur.lastrowid

async def send_media(chat_id, file_id, kind, caption, markup, context):
    kwargs = {"chat_id": chat_id, "caption": caption, "parse_mode": ParseMode.HTML, "reply_markup": markup}
    if kind == "video": return await context.bot.send_video(video=file_id, **kwargs)
    if kind == "audio": return await context.bot.send_audio(audio=file_id, **kwargs)
    if kind == "document_audio": return await context.bot.send_document(document=file_id, **kwargs)
    if kind == "photo": return await context.bot.send_photo(photo=file_id, **kwargs)

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.message, update.effective_user
    if msg.photo and context.user_data.pop("await_profile_photo", False):
        photo_id = msg.photo[-1].file_id
        db.execute("UPDATE users SET profile_photo_id=? WHERE user_id=?", (photo_id, user.id)); db.commit()
        await msg.reply_text("عکس پروفایل ذخیره شد.")
        return
    if msg.video: file_id, kind = msg.video.file_id, "video"
    elif msg.audio: file_id, kind = msg.audio.file_id, "audio"
    elif msg.photo: file_id, kind = msg.photo[-1].file_id, "photo"
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio/"):
        file_id, kind = msg.document.file_id, "document_audio"
    else: return
    approved = 1 if user.id == OWNER_ID else 0
    media_id = save_media(file_id, kind, user, approved)
    if approved:
        await msg.reply_text(f"{e('approve')} ذخیره شد و وارد آرشیو شد.", parse_mode=ParseMode.HTML)
        return
    await msg.reply_text(f"{e('upload')} محتوای شما برای تأیید ارسال شد.", parse_mode=ParseMode.HTML)
    label = {"video":"ادیت", "photo":"عکس", "audio":"موزیک", "document_audio":"موزیک"}[kind]
    cap = f"{e('upload')} <b>محتوای جدید</b>\nفرستنده: {safe('@'+user.username if user.username else user.first_name)}\nشناسه: <code>{user.id}</code>\nنوع: {label}"
    await send_media(OWNER_ID, file_id, kind, cap, review_buttons(media_id), context)

async def review_callback(update, context):
    q=update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("اجازه دسترسی نداری.", show_alert=True); return
    _, mid, decision=q.data.split(":"); mid=int(mid); decision=int(decision)
    item=db.execute("SELECT sender_id FROM media WHERE id=? AND approved=0",(mid,)).fetchone()
    if not item: await q.answer("این محتوا قبلاً بررسی شده."); return
    sender=item[0]
    if decision:
        db.execute("UPDATE media SET approved=1 WHERE id=?",(mid,)); result=f"{e('approve')} محتوای شما تأیید شد."; answer="تأیید شد."
    else:
        db.execute("DELETE FROM votes WHERE media_id=?",(mid,)); db.execute("DELETE FROM media WHERE id=?",(mid,)); result=f"{e('reject')} محتوای شما رد شد."; answer="رد شد."
    db.commit()
    try: await context.bot.send_message(sender,result,parse_mode=ParseMode.HTML)
    except Exception: pass
    await q.answer(answer); await q.edit_message_reply_markup(reply_markup=None)

async def send_random(update, context, kinds, label, key):
    items=media_rows(kinds)
    if not items:
        await update.effective_message.reply_text(f"{e(key)} هنوز محتوایی در آرشیو نیست.",parse_mode=ParseMode.HTML); return
    mid,fid,kind,sender,username=random.choice(items)
    userrow=db.execute("SELECT role,color FROM users WHERE user_id=?",(sender,)).fetchone()
    role,color=userrow if userrow else ("Member","blue")
    uname = ("@" + username) if username and not username.startswith("@") else username
    # One concise creator line only, with the premium role badge before role/color.
    role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
    caption=f"{e(key)} <b>{label}</b>\nسازنده: <a href=\"tg://user?id={sender}\">{safe(uname)}</a> {e(role_icon)} <b>{safe(role)}</b> · <b>{safe(color)}</b>"
    await send_media(update.effective_chat.id,fid,kind,caption,vote_buttons(mid),context)

async def random_video(update,context): await send_random(update,context,("video",),"ادیت","edit")
async def random_music(update,context): await send_random(update,context,("audio","document_audio"),"موزیک","music")
async def random_photo(update,context): await send_random(update,context,("photo",),"عکس","photo")

async def vote_callback(update,context):
    q=update.callback_query; _,mid,v=q.data.split(":"); mid=int(mid); v=int(v)
    item=db.execute("SELECT sender_id FROM media WHERE id=? AND approved=1",(mid,)).fetchone()
    if not item: await q.answer("محتوا در دسترس نیست."); return
    if item[0]==q.from_user.id and q.from_user.id != OWNER_ID:
        await q.answer("نمی‌توانی به محتوای خودت رأی بدهی.",show_alert=True); return
    db.execute("INSERT INTO votes(media_id,voter_id,vote) VALUES(?,?,?) ON CONFLICT(media_id,voter_id) DO UPDATE SET vote=excluded.vote",(mid,q.from_user.id,v)); db.commit()
    await q.answer("رأی ثبت شد."); await q.edit_message_reply_markup(reply_markup=vote_buttons(mid))

def ranking_text(kind):
    kinds={"edit":("video",),"music":("audio","document_audio"),"photo":("photo",),"all":("video","audio","document_audio","photo")}[kind]
    marks=",".join("?" for _ in kinds)
    rows=db.execute(f"SELECT sender_id,MAX(username),COUNT(*) FROM media WHERE kind IN ({marks}) AND approved=1 GROUP BY sender_id",kinds).fetchall()
    stats=[]
    for uid,uname,count in rows:
        mids=[x[0] for x in db.execute(f"SELECT id FROM media WHERE sender_id=? AND kind IN ({marks}) AND approved=1",(uid,*kinds)).fetchall()]
        up=down=0
        for mid in mids:
            a,b=vote_counts(mid); up+=a; down+=b
        stats.append((uid,uname or str(uid),count,up,down))
    stats.sort(key=lambda x:(x[3],x[2]),reverse=True)
    title={"edit":"رتبه‌بندی ادیتورها","music":"رتبه‌بندی موزیک‌فرست‌ها","photo":"رتبه‌بندی عکاس‌ها","all":"رتبه‌بندی کلی"}[kind]
    if not stats: return f"{e('rank')} <b>{title}</b>\n\nهنوز داده‌ای ثبت نشده."
    out=f"{e('rank')} <b>{title}</b>\n\n"
    for i,(uid,name,count,up,down) in enumerate(stats[:10],1): out+=f"<b>{i}.</b> {safe('@'+name if not name.startswith('@') else name)}\nمحتوا: {count} | {e('like')} {up} | {e('dislike')} {down}\n\n"
    return out

def top_users_text():
    rows = db.execute("SELECT user_id,MAX(username),COUNT(*) FROM media WHERE approved=1 GROUP BY sender_id").fetchall()
    scored = []
    for uid, uname, uploads in rows:
        likes = db.execute("SELECT COUNT(*) FROM votes v JOIN media m ON m.id=v.media_id WHERE m.sender_id=? AND v.vote=1", (uid,)).fetchone()[0]
        scored.append((uid, uname or str(uid), uploads, likes, uploads + likes))
    scored.sort(key=lambda item: (item[4], item[3], item[2]), reverse=True)
    out = f"{e('brand')} {e('rank')} <b>برترین‌های i3l.exe</b>\n\n"
    if not scored:
        return out + "هنوز فعالیتی برای رتبه‌بندی ثبت نشده."
    for place, (uid, uname, uploads, likes, score) in enumerate(scored[:3], 1):
        name = uname if str(uname).startswith("@") else "@" + str(uname)
        role_row = db.execute("SELECT role FROM users WHERE user_id=?", (uid,)).fetchone()
        role = role_row[0] if role_row else "Member"
        role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
        out += f"<b>#{place}</b> <a href=\"tg://user?id={uid}\">{safe(name)}</a> {e(role_icon)}\nمحتوا: <b>{uploads}</b> · لایک: <b>{likes}</b> · امتیاز: <b>{score}</b>\n\n"
    return out

async def ranking(update,context):
    txt=update.effective_message.text
    kind="music" if txt=="رنک موزیک" else "photo" if txt=="رنک عکس" else "all" if txt=="رنک کلی" else "edit"
    await update.effective_message.reply_text(ranking_text(kind),parse_mode=ParseMode.HTML)

async def profile_command(update,context):
    target=update.effective_user
    if update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user:
        target=update.effective_message.reply_to_message.from_user
    ensure_user(target)
    own=target.id==update.effective_user.id
    photo = db.execute("SELECT profile_photo_id FROM users WHERE user_id=?", (target.id,)).fetchone()[0]
    if photo:
        await update.effective_message.reply_photo(photo, caption=profile_text(target.id), parse_mode=ParseMode.HTML, reply_markup=profile_keyboard(target.id,own))
    else:
        await update.effective_message.reply_text(profile_text(target.id),parse_mode=ParseMode.HTML,reply_markup=profile_keyboard(target.id,own))

async def start(update,context):
    ensure_user(update.effective_user)
    txt=f"{e('brand')} {e('brand2')} {e('brand3')} <b>به i3l.exe خوش اومدی</b>\n\nاینجا آرشیو ادیت، موزیک و عکس همراه با پروفایل و سیستم رتبه‌بندی در دسترسه. از منوی لمسی زیر بخش موردنظرت رو انتخاب کن. برای ثبت محتوا، فایل رو در گفت‌وگوی خصوصی بات بفرست تا بررسی بشه."
    await update.effective_message.reply_text(txt,parse_mode=ParseMode.HTML,reply_markup=main_menu())

def help_text():
    return (f"{e('brand')} {e('help')} <b>راهنمای رسمی i3l.exe</b>\n\n"
      f"{e('edit')} <b>ادیت</b> — دریافت یک ادیت تصادفی از آرشیو تأییدشده\n"
      f"{e('music')} <b>موزیک</b> — دریافت موزیک تصادفی\n"
      f"{e('photo')} <b>عکس</b> — دریافت عکس تصادفی\n"
      f"{e('profile')} <b>پروفایل</b> — مشاهده آمار و سطح حساب؛ برای دیدن پروفایل دیگران، روی پیامشان ریپلای کن و بنویس «پروفایل»\n"
      f"{e('rank')} <b>رنک و برترین‌ها</b> — مشاهده رتبه‌بندی ادیت، موزیک، عکس و فعالیت کلی\n\n"
      f"{e('upload')} <b>ثبت محتوا</b> — فایل ویدیویی، عکس یا موزیک را در پیوی بات ارسال کن؛ پس از بررسی مدیریت به آرشیو اضافه می‌شود.\n"
      f"{e('like')} <b>امتیازدهی</b> — برای هر محتوا یک رأی ثبت می‌شود و امکان تغییر رأی وجود دارد. دیسلایک فقط در آمار نمایش داده می‌شود و از سطح کم نمی‌کند.\n\n"
      f"{e('stats')} <b>سیستم سطح</b> — با ارسال محتوای تأییدشده و دریافت لایک، امتیاز فعالیتت افزایش پیدا می‌کند.")

async def help_command(update,context):
    await update.effective_message.reply_text(help_text(),parse_mode=ParseMode.HTML,reply_markup=main_menu())

async def edit_callback_message(q, text, reply_markup=None):
    msg = q.message
    if msg and (msg.photo or msg.video or msg.audio or msg.document):
        return await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    return await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

async def menu_callback(update,context):
    q=update.callback_query; await q.answer(); data=q.data
    if data=="menu:close":
        await edit_callback_message(q, f"{e('brand')} <b>پنل بسته شد.</b>", None)
    elif data=="menu:home":
        await edit_callback_message(q, f"{e('brand')} {e('brand2')} {e('brand3')} <b>منوی i3l.exe</b>", main_menu())
    elif data=="menu:profile":
        ensure_user(q.from_user)
        photo = db.execute("SELECT profile_photo_id FROM users WHERE user_id=?", (q.from_user.id,)).fetchone()[0]
        if photo:
            await q.message.reply_photo(photo, caption=profile_text(q.from_user.id), parse_mode=ParseMode.HTML, reply_markup=profile_keyboard(q.from_user.id,True))
        else:
            await edit_callback_message(q, profile_text(q.from_user.id), profile_keyboard(q.from_user.id,True))
    elif data in ("menu:edit","menu:music","menu:photo"):
        await q.edit_message_text("برای دریافت محتوا، دستور مربوطه را در گروهی که بات عضو آن است ارسال کن: ادیت، موزیک یا عکس.",reply_markup=InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data=="menu:stats":
        total=db.execute("SELECT COUNT(*) FROM media WHERE approved=1").fetchone()[0]
        edits=db.execute("SELECT COUNT(*) FROM media WHERE approved=1 AND kind='video'").fetchone()[0]
        music=db.execute("SELECT COUNT(*) FROM media WHERE approved=1 AND kind IN ('audio','document_audio')").fetchone()[0]
        photos=db.execute("SELECT COUNT(*) FROM media WHERE approved=1 AND kind='photo'").fetchone()[0]
        people=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        await q.edit_message_text(f"{e('stats')} <b>آمار i3l.exe</b>\n\nکاربران: {people}\nکل محتوا: {total}\nادیت: {edits}\nموزیک: {music}\nعکس: {photos}",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data=="menu:help":
        await q.edit_message_text(help_text(),parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data=="menu:tops":
        await q.edit_message_text(top_users_text(), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data=="menu:ranks":
        kb=InlineKeyboardMarkup([[btn("رنک ادیت", "rank:edit", "edit"),btn("رنک موزیک", "rank:music", "music")],[btn("رنک عکس", "rank:photo", "photo"),btn("رنک کلی", "rank:all", "rank")],[btn("بستن", "menu:close", "back", "danger")]])
        await q.edit_message_text("بخش رتبه‌بندی را انتخاب کن:",reply_markup=kb)
    elif data.startswith("rank:"):
        await q.edit_message_text(ranking_text(data.split(":")[1]),parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data.startswith("profile:"):
        _,action,uid=data.split(":"); uid=int(uid)
        if uid!=q.from_user.id: return
        if action=="colors":
            kb=InlineKeyboardMarkup([[btn("سبز", "color:green", "approve", "success"),btn("آبی", "color:blue", "profile", "primary"),btn("قرمز", "color:red", "reject", "danger")],[btn("بستن", "menu:close", "back", "danger")]])
            await q.edit_message_text("رنگ پروفایل را انتخاب کن:",reply_markup=kb)
        elif action=="bio":
            context.user_data["await_bio"]=True
            await edit_callback_message(q, "بیوی جدیدت را به صورت یک پیام متنی بفرست.", InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
        elif action=="photo":
            context.user_data["await_profile_photo"]=True
            await edit_callback_message(q, "عکس موردنظرت را همین‌جا ارسال کن.", InlineKeyboardMarkup([[btn("بستن", "menu:close", "back", "danger")]]))
    elif data.startswith("color:"):
        color=data.split(":")[1]; db.execute("UPDATE users SET color=? WHERE user_id=?",(color,q.from_user.id)); db.commit()
        await edit_callback_message(q, profile_text(q.from_user.id), profile_keyboard(q.from_user.id,True))

async def text_router(update,context):
    msg=update.effective_message; user=update.effective_user
    if not user: return
    ensure_user(user)
    if context.user_data.pop("await_bio",False):
        db.execute("UPDATE users SET bio=? WHERE user_id=?",(msg.text[:160],user.id)); db.commit()
        await msg.reply_text("بیو ذخیره شد."); return
    if msg.chat.type in ("group","supergroup"):
        if msg.text and msg.text.strip()=="پروفایل": await profile_command(update,context); return

async def count_group_messages(update, context):
    msg = update.effective_message
    if not msg or msg.chat.type not in ("group", "supergroup"):
        return
    db.execute("INSERT INTO group_chats(chat_id,title,chat_type,active) VALUES(?,?,?,1) "
               "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,chat_type=excluded.chat_type,active=1",
               (msg.chat_id, msg.chat.title or "گروه", msg.chat.type))
    db.commit()
    row = db.execute("SELECT message_count FROM group_counts WHERE chat_id=?", (msg.chat_id,)).fetchone()
    count = (row[0] if row else 0) + 1
    db.execute("INSERT INTO group_counts(chat_id,message_count) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET message_count=excluded.message_count", (msg.chat_id, count))
    db.commit()
    if count % AUTO_EDIT_EVERY == 0:
        items = media_rows(("video",))
        if items:
            mid, fid, kind, sender, username = random.choice(items)
            r = db.execute("SELECT role,color FROM users WHERE user_id=?", (sender,)).fetchone()
            role, color = r if r else ("Member", "blue")
            uname = ("@" + username) if username and not username.startswith("@") else username
            role_icon = "owner" if role == "Owner" else "admin" if role == "Admin" else "vip" if role == "VIP" else "profile"
            caption = f"{e('edit')} <b>انتخاب تصادفی i3l.exe</b>\nسازنده: <a href=\"tg://user?id={sender}\">{safe(uname)}</a>  {e(role_icon)} <b>{safe(role)}</b> · <b>{safe(color)}</b>"
            await send_media(msg.chat_id, fid, kind, caption, vote_buttons(mid), context)


async def announce_command(update, context):
    """Owner-only broadcast to every group where the bot has been seen/added."""
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("این دستور فقط در اختیار سازنده بات است.")
        return
    announcement = " ".join(context.args).strip()
    if not announcement:
        await update.effective_message.reply_text("متن اعلامیه را بعد از دستور بنویس.\nنمونه: /announce متن اعلامیه")
        return
    chats = db.execute("SELECT chat_id,title FROM group_chats WHERE active=1").fetchall()
    if not chats:
        await update.effective_message.reply_text("هنوز گروهی در فهرست بات ثبت نشده است.")
        return
    sent = failed = 0
    message = f"{e('brand')} {e('brand2')} {e('brand3')} <b>اعلامیه رسمی i3l.exe</b>\n\n{safe(announcement)}"
    for chat_id, title in chats:
        try:
            await context.bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            failed += 1
            logging.warning("Broadcast failed for %s (%s): %s", title, chat_id, exc)
            if "chat not found" in str(exc).lower() or "bot was kicked" in str(exc).lower():
                db.execute("UPDATE group_chats SET active=0 WHERE chat_id=?", (chat_id,))
    db.commit()
    await update.effective_message.reply_text(f"اعلامیه ارسال شد.\nموفق: {sent} گروه\nناموفق: {failed} گروه")

async def welcome(update,context):
    for member in update.message.new_chat_members:
        if member.id==context.bot.id:
            db.execute("INSERT INTO group_chats(chat_id,title,chat_type,active) VALUES(?,?,?,1) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,active=1", (update.effective_chat.id, update.effective_chat.title or "گروه", update.effective_chat.type))
            db.commit()
            await update.message.reply_text(f"{e('brand')} <b>شما بات i3l.exe رو اد کردین، خیلی خفنین</b>\n\nادیت، موزیک، عکس و رنک در دسترسه. برای راهنما /help رو بزنید.",parse_mode=ParseMode.HTML,reply_markup=main_menu()); break

def main():
    token=os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN تنظیم نشده!"); return
    app=Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help_command))
    app.add_handler(CommandHandler("profile",profile_command))
    app.add_handler(CommandHandler("announce",announce_command))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.VIDEO|filters.PHOTO|filters.AUDIO|filters.Document.AUDIO),receive_media))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,text_router))
    group=filters.ChatType.GROUPS
    app.add_handler(MessageHandler(group, count_group_messages), group=-1)
    app.add_handler(MessageHandler(group & filters.Regex(r"^ادیت$"),random_video))
    app.add_handler(MessageHandler(group & filters.Regex(r"^موزیک$"),random_music))
    app.add_handler(MessageHandler(group & filters.Regex(r"^عکس$"),random_photo))
    app.add_handler(MessageHandler(group & filters.Regex(r"^(رنک|رنک موزیک|رنک عکس|رنک کلی)$"),ranking))
    app.add_handler(MessageHandler(group & filters.TEXT & ~filters.COMMAND,text_router))
    app.add_handler(CallbackQueryHandler(review_callback,pattern=r"^review:"))
    app.add_handler(CallbackQueryHandler(vote_callback,pattern=r"^vote:"))
    app.add_handler(CallbackQueryHandler(menu_callback,pattern=r"^(menu:|rank:|profile:|color:)"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,welcome))
    print(f"{BOT_NAME} is online.")
    app.run_polling()

if __name__=="__main__":
    main()
