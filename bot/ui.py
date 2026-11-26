"""Presentation helpers: premium custom emojis and inline keyboard builders.

These are copied verbatim from the original ``main.py`` so the bot's look,
feeling and Persian UI text stay identical.
"""

from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BOT_NAME = "i3l.exe"

# Premium custom emoji IDs supplied by the owner.
EMOJI = {
    "brand": 4956770345555068016,
    "brand2": 4956358131773867289,
    "brand3": 4956698533701878973,
    "profile": 4938653911507534983,
    "admin": 5406711411541823609,
    "vip": 5253912783140960304,
    "owner": 5323261373801571717,
    "edit": 5924623057997007830,
    "music": 5190785418248876370,
    "photo": 5375074927252621134,
    "rank": 5409008750893734809,
    "help": 5226512880362332956,
    "like": 5255965021594203933,
    "dislike": 5377627447791466788,
    "approve": 5904704361182798355,
    "reject": 5161208387957950108,
    "stats": 5203993413346680064,
    "upload": 5318974936310627698,
    "delete": 6050913161131859293,
    "back": 6050826230993787980,
}


def e(key):
    eid = EMOJI.get(key)
    # Telegram requires a valid fallback character inside tg-emoji.
    return f'<tg-emoji emoji-id="{eid}">⭐</tg-emoji>' if eid else ""


def safe(value):
    return html.escape(str(value or "—"))


def btn(text, callback, icon="brand", style="primary"):
    kwargs = {"text": text, "callback_data": callback, "style": style}
    if EMOJI.get(icon):
        kwargs["icon_custom_emoji_id"] = str(EMOJI[icon])
    return InlineKeyboardButton(**kwargs)
