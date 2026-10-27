"""
Callback handlers for menus.
Uses new Database system instead of data.json.
"""
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNEL_ID
from database import Database
from logger import send_logs
from texts import get_text


router = Router()


def get_db(msg: types.Message) -> Database:
    return msg.bot.db


def main_menu_kb() -> InlineKeyboardBuilder:
    """Главное меню (28-09-26). Фасад из ДВУХ кнопок, как требует владелец:
    «МОЙ ДОСТУП» (цепочка получить->инструкция->бонус->работает) и
    «ПОДДЕРЖАТЬ ПРОЕКТ» (сразу URL на Сбер). Держать синхронно с start_cmd
    в user_handlers.py — иначе возврат «🏠 В меню» покажет старый вид."""
    from user_handlers import DONATE_URL
    kb = InlineKeyboardBuilder()
    kb.button(text="🔑 МОЙ ДОСТУП", callback_data="my_key")
    kb.button(text="💳 ПОДДЕРЖАТЬ ПРОЕКТ", url=DONATE_URL)
    kb.button(text="🔷 Selectel 3 ТБ", callback_data="sel_key")
    kb.button(text="🛡 TrustTunnel", callback_data="tt_key")
    kb.adjust(1)
    return kb


@router.callback_query(F.data == "main_menu")
async def main_menu_handler(callback: types.CallbackQuery):
    db: Database = get_db(callback.message)
    user_id = callback.from_user.id

    # Check ban
    if await db.is_banned(user_id):
        _bu = await db.get_user(user_id)
        from ban_text import banned_text
        await callback.message.answer(banned_text(
            (_bu or {}).get("unsubscribed_at"), (_bu or {}).get("ban_reason")
        ))
        await callback.answer()
        return

    keys_count = await db.get_keys_count()
    lang = callback.from_user.language_code or "ru"
    name = callback.from_user.first_name or ""

    start_text = get_text(
        lang,
        "start",
        name=name,
        keys=keys_count,
    )

    # 26-09-26: раньше edit_text шёл без reply_markup -> после «Домой» кнопки
    # исчезали. Теперь меню всегда с клавиатурой.
    reply_markup = main_menu_kb().as_markup()
    try:
        await callback.message.edit_text(start_text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        await callback.message.answer(start_text, reply_markup=reply_markup, parse_mode="HTML")
    await send_logs(message=callback.message, log="callback: main menu")
    await callback.answer()
