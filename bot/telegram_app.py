"""Builds and configures the python-telegram-bot ``Application``.

The handler registration below is copied verbatim from the original
``main.py`` so the bot's routing, filter precedence and command set are
unchanged. The only difference is that the Application is returned to the
webhook runtime instead of being driven by a polling loop.
"""

from __future__ import annotations

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import handlers as H
from .runtime import settings


def bot_commands():
    """The bot's command list, published via set_my_commands so that typing
    '/' in a chat shows the command suggestions."""
    return [
        BotCommand("start", "شروع بات و راهنمای اولیه"),
        BotCommand("help", "راهنمای کامل دستورات و قابلیت‌ها"),
        BotCommand("profile", "مشاهده پروفایل (برای دیگران روی پیامشان ریپلای کن)"),
        BotCommand("cancel", "لغو عملیات در حال انتظار"),
        BotCommand("manage", "پنل مدیریت (فقط سازنده و ادمین)"),
        BotCommand("setreviewgroup", "تنظیم گروه بررسی (فقط Owner، داخل گروه مدیریت)"),
        BotCommand("restoremedia", "بازگرداندن محتوا حذف‌شده به آرشیو (فقط Owner)"),
    ]


def build_application() -> Application:
    """Create a fully wired (but not started) Telegram Application."""
    app = Application.builder().token(settings.bot_token).build()

    app.add_handler(CommandHandler("start", H.start))
    app.add_handler(CommandHandler("help", H.help_command))
    app.add_handler(CommandHandler("profile", H.profile_command))
    app.add_handler(CommandHandler("announce", H.announce_command))
    app.add_handler(CommandHandler("manage", H.admin_panel_command))
    app.add_handler(CommandHandler("cancel", H.cancel_waiting_command))
    app.add_handler(CommandHandler("setreviewgroup", H.set_review_group))
    app.add_handler(CommandHandler("restoremedia", H.restore_media_command))

    private_media = filters.ChatType.PRIVATE & (
        filters.VIDEO | filters.PHOTO | filters.AUDIO | filters.Document.AUDIO
    )
    app.add_handler(MessageHandler(private_media, H.receive_media))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, H.text_router))

    group = filters.ChatType.GROUPS
    group_media = group & (
        filters.VIDEO | filters.PHOTO | filters.AUDIO | filters.Document.AUDIO
    )
    # Media sent in a group chat is submitted for admin review (approve/reject
    # via inline buttons posted back into the same group).
    app.add_handler(MessageHandler(group_media, H.receive_media))
    # Run message counting before command-like text handlers.
    app.add_handler(MessageHandler(group, H.count_group_messages), group=-1)
    app.add_handler(MessageHandler(group & filters.Regex(r"^ادیت$"), H.random_video))
    app.add_handler(MessageHandler(group & filters.Regex(r"^موزیک$"), H.random_music))
    app.add_handler(MessageHandler(group & filters.Regex(r"^عکس$"), H.random_photo))
    app.add_handler(MessageHandler(group & filters.Regex(r"^رنک$"), H.ranking))
    app.add_handler(MessageHandler(group & filters.Regex(r"^رنک (موزیک|عکس|کلی)$"), H.ranking))
    app.add_handler(MessageHandler(group & filters.TEXT & ~filters.COMMAND, H.text_router))

    app.add_handler(CallbackQueryHandler(H.review_callback, pattern=r"^review:"))
    app.add_handler(CallbackQueryHandler(H.vote_callback, pattern=r"^vote:"))
    app.add_handler(CallbackQueryHandler(H.admin_panel_callback, pattern=r"^admin:panel$"))
    app.add_handler(CallbackQueryHandler(H.role_action_callback, pattern=r"^role:"))
    app.add_handler(CallbackQueryHandler(H.media_delete_callback, pattern=r"^media:delete$"))
    app.add_handler(CallbackQueryHandler(H.cancel_waiting_callback, pattern=r"^cancel:waiting$"))
    app.add_handler(CallbackQueryHandler(H.review_group_callback, pattern=r"^admin:reviewgroup$"))
    app.add_handler(CallbackQueryHandler(H.menu_callback, pattern=r"^(menu:|rank:|top:|profile:|color:)"))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, H.welcome))

    return app
