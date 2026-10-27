"""
User-facing handlers: /start, key generation, subscription check.
Replaces static GitHub key fetch with 3x-ui API client creation.
"""
import asyncio
import html
import json
import re
import logging
import time
import os as _os
from datetime import datetime

from aiogram.filters import Command
from aiogram import types, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from texts import get_text
from my_captcha import generate_captcha
from states import CaptchaState
from config import CHANNEL_ID, ADMIN_ID, EXPIRE_DAYS, PANEL_RU_URL, PANEL_RU_API_TOKEN, RU_INBOUND_IDS, CASCADE_HOST, RESUBSCRIBE_BAN_DAYS, SUB_URL, VIP_CHANNEL_ID, VIP_INVITE_URL, VIP_TOTAL_BYTES
from logger import logger, check_subscription, check_vip_subscription, send_logs
from database import Database
from panel_api import PanelSession, PanelAPIError
from autoreply import get_autoreply

router = Router()

# 28-09-26: ссылка на поддержку поднята наверх — теперь это URL-кнопка прямо
# в главном меню («💳 ПОДДЕРЖАТЬ ПРОЕКТ»), без текста-прокладки.
DONATE_URL = _os.getenv("DONATE_URL", "https://pay.mysbertips.ru/42794095")

def get_db(message: types.Message) -> Database:
    return message.bot.db


def get_panel(message: types.Message) -> PanelSession:
    return message.bot.panel


def get_panel_ru(message: types.Message) -> PanelSession:
    """Panel session for the RU (cascade entry) server — lazy init."""
    if getattr(message.bot, "panel_ru", None) is None:
        message.bot.panel_ru = PanelSession(
            base_url=PANEL_RU_URL,
            api_token=PANEL_RU_API_TOKEN,
            inbound_ids=RU_INBOUND_IDS,
        )
    return message.bot.panel_ru


def get_panel_sel(message: types.Message) -> PanelSession:
    """Panel session for Selectel (отдельный канал 3 ТБ) — lazy init."""
    if getattr(message.bot, "panel_sel", None) is None:
        from config import SEL_PANEL_URL, SEL_API_TOKEN, SEL_INBOUND_ID
        message.bot.panel_sel = PanelSession(
            base_url=SEL_PANEL_URL,
            api_token=SEL_API_TOKEN,
            inbound_ids=[SEL_INBOUND_ID],
        )
    return message.bot.panel_sel


async def get_lang(message: types.Message) -> str:
    """Get user's language code."""
    return message.from_user.language_code or "ru"


# ===================== KEY TEXT HELPERS =====================

def channel_name(link: str, index: int) -> str:
    """Human name for a channel link, matched by host/port (not by position)."""
    host_auth, port = "", 0
    try:
        authority = link.split("://", 1)[1].split("?", 1)[0].split("@", 1)[1]
        host_auth = authority.rsplit(":", 1)[0]
        port = int(authority.rsplit(":", 1)[1])
    except Exception:
        pass
    query = {}
    if "?" in link:
        for kv in link.split("?", 1)[1].split("#", 1)[0].split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                query[k] = v
    host = query.get("host", host_auth)
    if port == 2053:
        return "🌐 WS Cloudflare (резерв)"
    if port == 2087:
        return "🚀 Каскад (прямой WS)"
    if port == 443 and ("cdn." in host or "cdn." in host_auth):
        return "🚀 Каскад 443 (WS)"
    if port == 443:
        return "📱 Мобильный (Reality)"
    if port == 8443:
        return "🏠 Домашний интернет (Reality)"
    if port == 2083:
        return "🛡️ Резервный (Trojan)"
    if port == 2086:
        return "🚀 Быстрый маршрут"
    if port == 8444:
        return "🐢 Для старых устройств (WS)"
    return f"Канал {index + 1}"

def parse_links(links_json: str, fallback: str) -> list:
    """Parse stored links JSON; fallback to single link."""
    try:
        links = json.loads(links_json or "[]")
        if isinstance(links, list) and links:
            return links
    except (json.JSONDecodeError, TypeError):
        pass
    return [fallback] if fallback else []


def subscription_url(user_id: int) -> str:
    """Per-user subscription link: all 5 channels in one importable JSON list."""
    return SUB_URL.format(user_id=user_id)


def subscription_block(user_id: int) -> str:
    """Message block telling the user to import the subscription link."""
    return (
        "📥 <b>Доступ (все маршруты сразу)</b>\n"
        "Вставьте ссылку в ваше приложение как есть.\n"
        "Если приложение просит особый формат — допишите в конце ссылки <code>?f=happ</code>\n\n"
        f"<code>{subscription_url(user_id)}</code>\n\n"
        "Маршруты в доступе:\n"
        "• 🚀 Маршрут 1 — основной, самый быстрый\n"
        "• 🚀 Маршрут 2 — универсальный\n"
        "• 🌐 Маршрут 3 — запасной\n\n"
        "Если что-то перестало работать — обновите доступ."
    )


def format_links_text(links: list) -> str:
    """Format all connection links into a readable block."""
    if not links:
        return ""
    parts = []
    for i, link in enumerate(links):
        name = channel_name(link, i)
        parts.append(f"{name}:\n<code>{link}</code>")
    return "\n\n".join(parts)


def all_keys_block(
    user_id: int,
    links: list,
    used_gb: float,
    total_gb: float,
    expiry_date: str,
    prefix: str = None,
) -> str:
    """
    Full key issuance block: subscription link + EVERY key listed at once.
    Replaces the old step-by-step (РАБОТАЕТ/НЕ РАБОТАЕТ) flow — the owner
    decided (21-08-26) users get the whole list immediately.
    """
    head = f"{prefix}\n\n" if prefix else ""
    return (
        f"{head}{subscription_block(user_id)}\n\n"
        f"🔑 Всё, что нужно — ссылка выше: она открывает все каналы сразу.\n\n"
        f"📊 {used_gb:.1f} / {total_gb:.0f} ГБ\n"
        f"📅 действует до: {expiry_date}\n"
        f"📱 Лимит устройств: 2\n\n"
        f"Не отписывайся — и доступ будет жить 😉\n\n"
        f"━━━━━━━━━━━\n"
        f"Зашло — можно скинуть на чай кнопкой ниже 🍵"
    )


def keys_kb() -> InlineKeyboardBuilder:
    """Keyboard under the all-keys message.

    22-09-26 (антитупняк): первой — «📋 Скопировать ссылку» (кладёт URL в буфер,
    как просили в 4 из 5 свежих обращений: «а где доступ», «куда вставлять»),
    второй — «📖 Как подключить». ДОНАТ убран с этого экрана (остаётся в меню)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 ОБНОВИТЬ КЛЮЧ", callback_data="refresh_key")
    builder.button(text="📋 Скопировать ссылку", callback_data="copy_sub")
    builder.button(text="📖 Как подключить", callback_data="help")
    builder.button(text="💵 СКИНУТЬ НА ЧАЙ", callback_data="donate")
    builder.button(text="🛡 TrustTunnel", callback_data="tt_key")
    builder.button(text="🏠 В меню", callback_data="main_menu")
    builder.adjust(1)
    return builder


def home_button(builder: InlineKeyboardBuilder) -> InlineKeyboardBuilder:
    """Единая кнопка возврата в главное меню (26-09-26).
    Добавляется последней на всех экранах-подменю, чтобы всегда можно было
    вернуться домой одним тапом, не нажимая /start."""
    builder.button(text="🏠 В меню", callback_data="main_menu")
    return builder


def links_as_file(links: list) -> bytes:
    """Serialize links into a text file (import-ready, one per line)."""
    return "\n".join(links).encode("utf-8")


HELP_TEXT = (
    "📖 <b>ПОЛНАЯ ИНСТРУКЦИЯ</b>\n\n"
    "Мы вынесли её в закрытый канал — там пошагово и всегда свежая версия.\n"
    f"Ссылка: {VIP_INVITE_URL or '—'}\n\n"
    "Коротко, самое нужное:\n"
    "1. В меню бота нажмите «🎲 ПОЛУЧИТЬ ДОСТУП» — бот пришлёт вашу персональную ссылку.\n"
    "2. Меню → «🔑 МОЙ ДОСТУП» → «📋 Скопировать».\n"
    "3. Вставьте ссылку в приложение и потяните список профилей вниз.\n\n"
    "⚠️ Перевыпуск доступа нужен только если совсем ничего не помогает."
)


TROUBLE_TEXT = (
    "🛡 <b>НЕ ПОДКЛЮЧАЕТСЯ?</b>\n\n"
    "Чаще всего дело в настройках приложения или в нестабильной сети — а не в самом доступе.\n\n"
    "Что попробовать:\n"
    "1) обновить ссылку и потянуть список маршрутов вниз;\n"
    "2) выбрать другой маршрут;\n"
    "3) проверить на Wi-Fi и на мобильной сети;\n"
    "4) перезапустить приложение.\n\n"
    "Не помогло — напишите нам, разберёмся вместе.\n"
    "Подробная инструкция — в закрытом канале.\n"
    f"Ссылка: {VIP_INVITE_URL or '—'}"
)


@router.callback_query(F.data == "trouble_help")
async def trouble_help_callback(callback: types.CallbackQuery):
    """Короткая подсказка на случай проблем с подключением."""
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔑 МОЙ ДОСТУП", callback_data="my_key")
    kb.button(text="◀️ Назад", callback_data="help")
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    try:
        await callback.message.edit_text(TROUBLE_TEXT, reply_markup=kb.as_markup(),
                                         parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(TROUBLE_TEXT, reply_markup=kb.as_markup(),
                                      parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    """Инструкция по использованию. Шаг цепочки: инструкция -> бонус -> «работает»."""
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 ПОЛНАЯ ИНСТРУКЦИЯ (закрытый канал)", url=VIP_INVITE_URL or "#")
    builder.button(text="🛡 НЕ ПОДКЛЮЧАЕТСЯ? НАПИШИТЕ НАМ", callback_data="trouble_help")
    builder.button(text="🎁 БОНУС", callback_data="vip_info")
    builder.button(text="✅ ЕСЛИ РАБОТАЕТ — ХОРОШО", callback_data="works_ok")
    builder.button(text="🏠 В меню", callback_data="main_menu")
    builder.adjust(1)
    try:
        await callback.message.edit_text(HELP_TEXT, reply_markup=builder.as_markup(),
                                         parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(HELP_TEXT, reply_markup=builder.as_markup(),
                                      parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "works_ok")
async def works_ok_callback(callback: types.CallbackQuery):
    """«Если работает — хорошо». Без пафоса: короткое подтверждение и всё."""
    await callback.answer("Отлично 👍")
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    txt = "👍 Хорошо. Если что-то отвалится — жми «МОЙ ДОСТУП» или пиши в поддержку."
    try:
        await callback.message.edit_text(txt, reply_markup=kb.as_markup())
    except Exception:
        await callback.message.answer(txt, reply_markup=kb.as_markup())
    await send_logs(message=callback.message, log="цепочка: работает-ок",
                    user_id=callback.from_user.id)

# ===================== БОНУСНЫЙ КАНАЛ =====================
# 28-09-26: «VIP» из текстов убрано (пафос по просьбе владельца). Только «бонус».
# Логика НЕ менялась: подписан на закрытый канал -> инбаунд 8 (финка, /bonus-ws);
# отписался -> доступ гаснет сам. Денег тут нет, это не чаевые.

BONUS_DISCLAIMER = (
    "🎁 <b>Бонусный канал</b>\n\n"
    "Закрытый канал — {link}\n\n"
    "Подписчикам бонусом ещё один канал до момента отписки."
)


SEL_PAY_TEXT = (
    "🔷 <b>Канал Selectel 3 ТБ</b>\n\n"
    "Отдельный канал на 3 ТБ — оплачивается отдельно от основного доступа.\n\n"
    "Нажми 👇 — чаевые в Сбер, после чаевых нажми «✅ Я скинул на чай»."
)


@router.callback_query(F.data == "sel_key")
async def sel_key_callback(callback: types.CallbackQuery):
    """01-10-26: кнопка ведёт в Сбер (как чаевые), доступ сразу НЕ выдаётся."""
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 СКИНУТЬ НА ЧАЙ", url=DONATE_URL)
    kb.button(text="✅ Я скинул на чай", callback_data="sel_issue")
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    try:
        await callback.message.edit_text(SEL_PAY_TEXT, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(SEL_PAY_TEXT, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    await send_logs(message=callback.message, log="sel: показана чаевые", user_id=callback.from_user.id)


@router.callback_query(F.data == "sel_issue")
async def sel_issue_callback(callback: types.CallbackQuery):
    """«Я скинул на чай» по Selectel: пишем заявку в БД (claimed), затем выдаём канал.
    Владелец потом сверяет базу и банит врунов."""
    await callback.answer()
    user_id = callback.from_user.id
    username = callback.from_user.username or ""
    full_name = callback.from_user.full_name or ""
    # 01-10-26: фиксируем нажатие «Я скинул на чай» в payments (status=claimed).
    try:
        pid = await get_db(callback.message).add_payment(
            user_id, username, full_name, 0, method="sel_claim"
        )
        logger.info(f"SEL claim recorded: tg={user_id} payment_id={pid}")
    except Exception as e:
        logger.warning(f"SEL claim not recorded: {e}")
    try:
        panel_sel = get_panel_sel(callback.message)
        res = await panel_sel.add_sel_client(user_id)
    except Exception as e:
        logger.error(f"SEL issue failed for {user_id}: {e}")
        res = None
    if not res or not res.get("link"):
        await callback.message.answer("⚠️ Не удалось выдать канал. Попробуй позже.")
        return
    txt = (
        "🔷 <b>Канал Selectel 3 ТБ</b>\n\n"
        "Твоя ссылка:\n<code>" + res["link"] + "</code>\n\n"
        "Вставь её в своё приложение."
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    try:
        await callback.message.edit_text(txt, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(txt, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    # уведомить админа
    try:
        await callback.bot.send_message(
            ADMIN_ID,
            f"🔷 Selectel 3 ТБ\ntg_id=<code>{user_id}</code>\n@{username or '-'}\n"
            f"email={res['email']}"
        )
    except Exception as e:
        logger.warning(f"admin notify failed: {e}")


def vip_kb(is_sub: bool = False) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if is_sub:
        builder.button(text="🎁 ВЫДАТЬ БОНУС КАНАЛ", callback_data="vip_bonus")
    else:
        builder.button(text="📢 ЗАКРЫТЫЙ КАНАЛ", url=VIP_INVITE_URL or "#")
        builder.button(text="🎁 ВЫДАТЬ БОНУС КАНАЛ", callback_data="vip_bonus")
    builder.button(text="🏠 В меню", callback_data="main_menu")
    builder.adjust(1)
    return builder


@router.callback_query(F.data == "vip_info")
async def vip_info_callback(callback: types.CallbackQuery):
    """Показать дисклеймер про бонус."""
    await callback.answer()
    sub = await check_vip_subscription(callback.bot, callback.from_user.id)
    try:
        await callback.message.edit_text(
            BONUS_DISCLAIMER.format(link=VIP_INVITE_URL or "#"),
            reply_markup=vip_kb(sub).as_markup(),
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            BONUS_DISCLAIMER.format(link=VIP_INVITE_URL or "#"),
            reply_markup=vip_kb(sub).as_markup(),
            parse_mode="HTML", disable_web_page_preview=True,
        )


@router.callback_query(F.data == "vip_bonus")
async def vip_bonus_callback(callback: types.CallbackQuery):
    """Выдать бонусный канал: проверка доступа на закрытый канал -> выдача."""
    await callback.answer()
    user_id = callback.from_user.id
    bot = callback.bot

    # 1. доступ на закрытый канал (fail-closed)
    is_sub = await check_vip_subscription(bot, user_id)
    if not is_sub:
        txt = (
            "🚫 Друг, тебя нет на закрытом канале.\n\n"
            f"Подпишись здесь: {VIP_INVITE_URL or '#'}\n\n"
            "Потом вернись и нажми кнопку снова."
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 ПОДПИСАТЬСЯ", url=VIP_INVITE_URL or "#")
        kb.button(text="🎁 Я ПОДПИСАЛСЯ", callback_data="vip_bonus")
        kb.button(text="🏠 В меню", callback_data="main_menu")
        kb.adjust(1)
        try:
            await callback.message.edit_text(txt, reply_markup=kb.as_markup(),
                                             disable_web_page_preview=True)
        except Exception:
            await callback.message.answer(txt, reply_markup=kb.as_markup(),
                                          disable_web_page_preview=True)
        await send_logs(message=callback.message, log="бонус: не подписан", user_id=user_id)
        return

    # 2. выдать/переиспользовать бонусного приложения (инбаунд 8 на финке)
    panel = get_panel(callback.message)
    panel_ru = get_panel_ru(callback.message)
    try:
        res = await panel.add_vip_client(user_id)
    except Exception as e:
        logger.error(f"bonus issue failed for {user_id}: {e}")
        res = None

    if not res:
        await callback.message.answer("⚠️ Не удалось выдать бонус. Попробуйте позже.")
        await send_logs(message=callback.message, log="бонус: ошибка выдачи", user_id=user_id)
        return

    # [bonussync] записать/обновить бонусный VIP-доступ в базе бота (учёт)
    try:
        _db = get_db(callback.message)
        _u = await _db.get_user(user_id)
        if not _u:
            await _db.add_user(
                telegram_id=user_id,
                username=callback.from_user.username or "",
                email=res["email"],
                client_uuid=res["uuid"],
                vless_link=res["link"],
                total_bytes=VIP_TOTAL_BYTES,
                expiry_at=int(time.time() * 1000) + EXPIRE_MS,
                links=[res["link"]],
            )
            logger.info(f"[bonussync] {res['email']} записан в базу бота")
        else:
            # юзер уже есть -> ДОБАВИТЬ/обновить VIP-доступ в его links, не потеряв остальные
            try:
                _raw = _u["links"] if isinstance(_u, dict) else None
                _cur = []
                if _raw:
                    try: _cur = json.loads(_raw) if isinstance(_raw, str) else list(_raw)
                    except Exception: _cur = []
                _cur = [x for x in _cur if "/ws-hidden" in x and "443" in x]  # убрать старый VIP
                if res["link"] not in _cur:
                    _cur.append(res["link"])
                await _db.update_user_links(user_id, _cur)
                logger.info(f"[bonussync] VIP-доступ {res['email']} добавлен в links пользователя {user_id}")
            except Exception as _e2:
                logger.error(f"[bonussync] update links failed {user_id}: {_e2}")
    except Exception as _e:
        logger.error(f"[bonussync] не удалось записать {res.get('email')} в базу: {_e}")

    gb = VIP_TOTAL_BYTES / (1024 ** 3)
    txt = (
        "🎁 <b>Бонусный канал выдан!</b>\n\n"
        "Ваша отдельная ссылка (добавьте её в приложение отдельным профилем):\n\n"
        f"<code>{res['link']}</code>\n\n"
        f"📊 Лимит: {gb:.0f} ГБ\n"
        "🔓 Работает, пока вы в закрытом канале."
    )
    await callback.message.answer(txt, parse_mode="HTML", disable_web_page_preview=True)
    await send_logs(message=callback.message, log="бонус: выдан", user_id=user_id)


# =====================================================

# ===================== START =====================


@router.message(Command("start"))
async def start_cmd(message: types.Message):
    db: Database = get_db(message)
    user_id = message.from_user.id
    lang = await get_lang(message)
    name = html.escape(message.from_user.first_name or "")

    # Check ban
    if await db.is_banned(user_id):
        _bu = await db.get_user(user_id)
        from ban_text import banned_text
        await message.answer(banned_text(
            (_bu or {}).get("unsubscribed_at"), (_bu or {}).get("ban_reason")
        ))
        return

    # Check subscription
    subscribed = await check_subscription(message.bot, user_id, CHANNEL_ID)
    if not subscribed:
        await send_subscription_required(message, lang)
        return

    # Show main menu
    await db.touch_user(user_id)   # last_seen + visits: отслеживаем возвраты
    keys_count = await db.get_keys_count()
    user = await db.get_user(user_id)

    start_text = get_text(lang, "start", name=name, keys=keys_count)

    builder = InlineKeyboardBuilder()
    # 28-09-26 (фасад, требование владельца): ДВЕ кнопки.
    # «МОЙ ДОСТУП» ведёт цепочкой: получить -> инструкция -> бонус -> «работает».
    # «ПОДДЕРЖАТЬ ПРОЕКТ» — сразу URL на Сбер, без текста-прокладки.
    builder.button(text="🔑 МОЙ ДОСТУП", callback_data="my_key")
    builder.button(text="💳 ПОДДЕРЖАТЬ ПРОЕКТ", url=DONATE_URL)
    builder.button(text="🔷 Selectel 3 ТБ", callback_data="sel_key")
    builder.button(text="🛡 TrustTunnel", callback_data="tt_key")
    builder.adjust(1)

    await message.answer(
        start_text,
        reply_markup=builder.as_markup(),
    )
    await send_logs(message=message, log="start")


@router.message(Command("donate"))
async def donate_cmd(message: types.Message):
    """Показать кнопку чаевых (чаевые)."""
    await message.answer(DONATE_TEXT, reply_markup=donate_keyboard().as_markup(),
                         parse_mode="HTML", disable_web_page_preview=True)
    await send_logs(message=message, log="donate shown")


@router.callback_query(F.data == "donate")
async def donate_callback(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(DONATE_TEXT, reply_markup=donate_keyboard().as_markup(),
                                  parse_mode="HTML", disable_web_page_preview=True)
    await send_logs(message=callback.message, log="donate shown (callback)", user_id=callback.from_user.id)


# --- Поддержка проекта: чаевые (ссылка в банк) ---
# 28-09-26: DONATE_URL переехал наверх файла (нужен в главном меню).
# Ручные реквизиты (DEV_CARD) и «Я перевёл» убраны 27-09 — кнопка сразу в банк.

DONATE_TEXT = (
    "💳 <b>Поддержать проект</b>\n\n"
    "Нажми 👇 — и проект живёт дальше."
)


def donate_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 СКИНУТЬ НА ЧАЙ", url=DONATE_URL)
    builder.button(text="🏠 В меню", callback_data="main_menu")
    builder.adjust(1)
    return builder


@router.message(Command("vpn"))
async def vpn_cmd(message: types.Message, state: FSMContext):
    """Alias for getting a key."""
    await generate_vpn_key(message, state)


@router.callback_query(F.data == "get_key")
async def get_key_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # IMPORTANT: callback.message.from_user is the BOT (message author),
    # so the real user id must come from callback.from_user.
    await generate_vpn_key(callback.message, state, user_id=callback.from_user.id)


@router.callback_query(F.data == "copy_sub")
async def copy_sub_callback(callback: types.CallbackQuery):
    """Отдаёт ссылку ОТДЕЛЬНЫМ сообщением: её удобно копировать тапом.

    22-09-26: юзеры вставляли ссылку В ЧАТ и ждали подключения. Отдельное
    короткое сообщение со <code>-строкой копируется одним тапом, плюс явное
    «вставим?» -> сразу показываем инструкцию."""
    await callback.answer()
    user_id = callback.from_user.id
    url = subscription_url(user_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Что делать дальше (инструкция)", callback_data="help")
    kb.button(text="🔄 Другой формат ссылки", callback_data="copy_happ")
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    await callback.message.answer(
        "📋 <b>Ваша ссылка</b> (нажмите на неё — скопируется):\n\n"
        f"<code>{url}</code>\n\n"
        "⚠️ Вставлять её нужно <b>в приложение</b>, а не в этот чат.\n"
        "В чате интернета нет — чат только выдаёт ссылку.",
        reply_markup=kb.as_markup(),
        disable_web_page_preview=True,
    )
    await send_logs(message=callback.message, log="copy_sub shown", user_id=user_id)


@router.callback_query(F.data == "copy_happ")
async def copy_happ_callback(callback: types.CallbackQuery):
    """Тот же адрес с ?f=happ — для приложений, которые просят такой формат."""
    await callback.answer()
    user_id = callback.from_user.id
    url = subscription_url(user_id) + "?f=happ"
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Что делать дальше (инструкция)", callback_data="help")
    kb.button(text="🏠 В меню", callback_data="main_menu")
    kb.adjust(1)
    await callback.message.answer(
        "📋 <b>Ссылка в другом формате</b> (нажмите — скопируется):\n\n"
        f"<code>{url}</code>\n\n"
        "Вставлять — в приложение, в раздел «Добавить доступ / Импорт из буфера».",
        reply_markup=kb.as_markup(),
        disable_web_page_preview=True,
    )
    await send_logs(message=callback.message, log="copy_happ shown", user_id=user_id)


@router.callback_query(F.data == "my_key")
async def my_key_callback(callback: types.CallbackQuery):
    await callback.answer()
    db: Database = get_db(callback.message)
    user = await db.get_user(callback.from_user.id)

    if not user or not user["is_active"]:
        kb = InlineKeyboardBuilder()
        kb.button(text="🎲 ПОЛУЧИТЬ ДОСТУП", callback_data="get_key")
        kb.button(text="🏠 В меню", callback_data="main_menu")
        kb.adjust(1)
        await callback.message.edit_text(
            "🔑 Доступа пока нет.\n\n"
            "Нажми «🎲 ПОЛУЧИТЬ ДОСТУП» — бот выдаст твою ссылку сразу.",
            reply_markup=kb.as_markup(),
        )
        return

    links = parse_links(user.get("links", ""), user["vless_link"])
    used_gb = user["used_bytes"] / (1024**3)
    total_gb = user["total_bytes"] / (1024**3)
    expiry_date = datetime.fromtimestamp(user["expiry_at"] / 1000).strftime("%d.%m.%Y")

    text = all_keys_block(callback.from_user.id, links, used_gb, total_gb, expiry_date)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 ОБНОВИТЬ КЛЮЧ", callback_data="refresh_key")
    builder.button(text="📖 ИНСТРУКЦИЯ", callback_data="help")
    builder.button(text="💵 Поддержать проект", callback_data="donate")
    builder.button(text="🏠 В меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    return


@router.callback_query(F.data == "refresh_key")
async def refresh_key_callback(callback: types.CallbackQuery):
    """Переприменить ключ юзера в панели(ях) и показать фактический результат."""
    db: Database = get_db(callback.message)
    user = await db.get_user(callback.from_user.id)
    if not user or not user["is_active"]:
        await callback.answer("Доступа нет — сначала получи ключ.", show_alert=True)
        return
    await callback.answer("Обновляю ключ…")

    panel: PanelSession = get_panel(callback.message)
    panel_ru: PanelSession = get_panel_ru(callback.message)
    email = user["email"]

    res = {}
    for name, p in (("RU", panel_ru), ("FIN", panel)):
        try:
            res[name] = await p.refresh_client(email)
        except Exception as e:
            res[name] = {"ok": False, "reason": f"error: {e}"}
    logger.info(f"refresh_key {email}: {res}")

    good = [k for k, v in res.items() if v.get("ok")]
    if good:
        exp = max((res[k].get("expiry") or 0) for k in good)
        if exp:
            await db.update_user_expiry(callback.from_user.id, exp)
        when = datetime.now().strftime("%H:%M:%S")
        tail = f"\n📅 Срок до {datetime.fromtimestamp(exp / 1000).strftime('%d.%m.%Y')}" if exp else ""
        await callback.message.answer(
            f"✅ Ключ обновлён в {when} ({', '.join(good)}){tail}\n\n"
            "Если приложение не подключается — отключи профиль и включи заново."
        )
    else:
        detail = ", ".join(f"{k}: {v.get('reason')}" for k, v in res.items())
        await callback.message.answer(
            f"⚠️ Обновить не удалось ({detail}).\n"
            "Ключа нет в панели — напиши нам, выдадим заново."
        )


# ===================== KEY GENERATION =====================


async def generate_vpn_key(message: types.Message, state: FSMContext = None, user_id: int = None):
    db: Database = get_db(message)
    panel: PanelSession = get_panel(message)
    panel_ru: PanelSession = get_panel_ru(message)
    if user_id is None:
        user_id = message.from_user.id  # only safe for direct /vpn command
    username = message.from_user.username or ""
    lang = await get_lang(message)

    # Check ban
    if await db.is_banned(user_id):
        _bu = await db.get_user(user_id)
        from ban_text import banned_text
        await message.answer(banned_text(
            (_bu or {}).get("unsubscribed_at"), (_bu or {}).get("ban_reason")
        ))
        return

    # Check subscription
    subscribed = await check_subscription(message.bot, user_id, CHANNEL_ID)
    if not subscribed:
        await send_subscription_required(message, lang)
        return

    # Resubscribe ban: a user who unsubscribed (and was disabled for it) cannot
    # get a new key for RESUBSCRIBE_BAN_DAYS days since the disable moment.
    existing = await db.get_user(user_id)
    if existing and not existing["is_active"]:
        banned_until = (existing.get("unsubscribed_at") or 0) + RESUBSCRIBE_BAN_DAYS * 24 * 3600 * 1000
        if banned_until > time.time() * 1000:
            days_left = int((banned_until - time.time() * 1000) / (24 * 3600 * 1000)) + 1
            await message.answer(
                f"⏳ Вы недавно отписались от канала.\n"
                f"Новый доступ можно получить через {days_left} дн."
            )
            await send_logs(message=message, user_id=user_id, log=f"resubscribe blocked ({days_left}d left)")
            return

    # Captcha gate: user must solve a captcha before getting a key.
    # Skip only when called right after a successful captcha solve.
    if user_id not in captcha_passed:
        await _request_captcha(message, user_id)
        return

    # Per-user lock: double-click on "Получить доступ" must not create
    # duplicate clients in the panels.
    locks: dict = getattr(message.bot, "user_locks", {})
    lock = locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        await message.answer("⏳ Доступ уже генерируется... Подождите секунду.")
        return
    async with lock:
        await _generate_vpn_key_locked(message, db, panel, panel_ru, user_id, username, lang)


async def _request_captcha(message: types.Message, user_id: int):
    """Generate and send a captcha image; remember the expected code."""
    code, buf = generate_captcha()
    captcha_store[user_id] = (code, time.time() + 120)  # valid 2 minutes
    logger.info(f"CAPTCHA sent to user {user_id}, code={code}")
    await message.answer_photo(
        types.BufferedInputFile(buf.getvalue(), filename="captcha.png"),
        caption="🤖 Введите цифры с картинки, чтобы получить доступ:",
    )


async def _generate_vpn_key_locked(message: types.Message, db, panel, panel_ru, user_id, username, lang):
    """Actual key generation, guarded by the per-user lock."""

    # Check for existing key
    existing = await db.get_user(user_id)
    if existing:
        if existing["is_active"]:
            # User already has an active key — make sure cascade links exist,
            # then show ALL keys at once (no step-by-step since 21-08-26).
            links = parse_links(existing.get("links", ""), existing["vless_link"])

            # Ensure cascade (RU) links are present for older users created
            # before the cascade channels were added.
            need_cascade = not any(
                CASCADE_HOST in l for l in links
            )
            if need_cascade:
                try:
                    cascade = await panel_ru.add_cascade_client(user_id, username)
                    if cascade and cascade.links:
                        links = list(cascade.links)
                        await db.update_user_links(user_id, links)
                        logger.info(f"Cascade links refreshed for existing user {user_id}")
                except Exception as e:
                    logger.error(f"Cascade add failed for {user_id}: {e}")

            used_gb = existing["used_bytes"] / (1024**3)
            total_gb = existing["total_bytes"] / (1024**3)
            expiry_date = datetime.fromtimestamp(existing["expiry_at"] / 1000).strftime("%d.%m.%Y")
            text = all_keys_block(user_id, links, used_gb, total_gb, expiry_date)
            await message.answer(text, reply_markup=keys_kb().as_markup())
            await send_logs(message=message, log="key re-issued (existing active)")
            return
        else:
            # User was disabled (unsubscribed or expired) but is subscribed now —
            # reactivate the existing client in the panel and renew it.
            new_expiry = int(time.time() * 1000) + EXPIRE_DAYS * 24 * 3600 * 1000
            # С 15-08-26 новые юзеры живут только на RU-панели; старые — на
            # финской + RU. Активируем там, где приложение реально существует.
            ok_fin = await panel.update_client(
                existing["email"], existing["client_uuid"],
                enable=True, expiry_time=new_expiry,
            )
            ok_ru = False
            try:
                ok_ru = bool(await panel_ru.update_client(
                    existing["email"], existing["client_uuid"],
                    enable=True, expiry_time=new_expiry,
                ))
            except Exception:
                pass
            if not (ok_fin or ok_ru):
                await message.answer(
                    "❌ Не удалось активировать доступ. Попробуйте позже."
                )
                return
            await db.update_user_expiry(user_id, new_expiry)
            await db.set_user_active(user_id, True)
            await db.clear_unsubscribed_at(user_id)
            links = parse_links(existing.get("links", ""), existing["vless_link"])
            expiry_date = datetime.fromtimestamp(new_expiry / 1000).strftime("%d.%m.%Y")
            text = all_keys_block(
                user_id,
                links,
                existing["used_bytes"] / (1024**3),
                existing["total_bytes"] / (1024**3),
                expiry_date,
                prefix="✅ <b>Доступ снова активен!</b>",
            )
            await message.answer(text, reply_markup=keys_kb().as_markup())
            await send_logs(message=message, log="key re-activated (re-subscribed)")
            return

    # Generate new key via 3x-ui panels (Finland + RU cascade)
    status_msg = await message.answer("🔄 Генерация доступов...")

    try:
        # АНТИ-ПАЛЁЖ (15-08-26, «всё прячь»): новым юзерам выдаём ТОЛЬКО
        # каскадные доступы (RU вход → финка выход). Прямые финские ссылки
        # больше не создаются и не выдаются — финский IP не светится.
        cascade = await panel_ru.add_cascade_client(user_id, username)
        links = list(cascade.links) if cascade else []
        if not links:
            raise PanelAPIError("no links generated")

        user_added = await db.add_user(
            telegram_id=user_id,
            username=username,
            email=cascade.email,
            client_uuid=cascade.uuid,
            vless_link=cascade.link,
            total_bytes=cascade.total_gb,
            expiry_at=cascade.expiry_time,
            links=links,
        )

        if not user_added:
            # Rollback: remove cascade client to avoid orphans.
            try:
                if cascade:
                    await panel_ru.delete_client(cascade.email)
            except Exception as e:
                logger.error(f"Rollback delete (RU) failed for {cascade.email}: {e}")
            await status_msg.edit_text("❌ Ошибка при сохранении. Попробуйте позже.")
            return

        await status_msg.delete()

        # Все доступы списком сразу (с 21-08-26 step-by-step убран).
        expiry_date = datetime.fromtimestamp(cascade.expiry_time / 1000).strftime("%d.%m.%Y")
        text = all_keys_block(
            user_id,
            links,
            0.0,
            cascade.total_gb / (1024**3),
            expiry_date,
            prefix="🎉 <b>Доступ готов!</b>",
        )
        await message.answer(text, reply_markup=keys_kb().as_markup())

        await send_logs(message=message, log="key generated via 3x-ui (all keys)")

    except PanelAPIError as e:
        logger.error(f"Panel API error for user {user_id}: {e}")
        await status_msg.edit_text(
            "❌ Ошибка при создании доступа. Панель 3x-ui временно недоступна.\n"
            "Пожалуйста, попробуйте позже."
        )
    except Exception as e:
        logger.error(f"Unexpected error for user {user_id}: {e}")
        await status_msg.edit_text(
            "❌ Непредвиденная ошибка. Попробуйте позже."
        )


# ===================== SUBSCRIPTION CHECK =====================


async def send_subscription_required(message: types.Message, lang: str):
    """Send a message asking user to subscribe to the channel."""
    channel_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID else "#"

    text = (
        "📢 <b>Для доступа к сервису необходимо подписаться на канал!</b>\n\n"
        "1. Нажмите кнопку «Подписаться»\n"
        "2. Вернитесь и нажмите «Проверить подписку»"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Подписаться", url=channel_link)
    builder.button(text="✅ Проверить подписку", callback_data="check_sub")
    builder.adjust(1)

    await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    await callback.answer()
    subscribed = await check_subscription(
        callback.bot, callback.from_user.id, CHANNEL_ID
    )
    if subscribed:
        await callback.message.edit_text("✅ Подписка подтверждена! Нажмите /start")
    else:
        await callback.answer(
            "❌ Вы ещё не подписались. Подпишитесь и попробуйте снова.",
            show_alert=True,
        )


# ===================== CAPTCHA (anti-bot gate) =====================

# user_id -> (expected_digits, expiry_ts). No FSM dependency —
# solved by matching against any incoming text while pending.
captcha_store: dict = {}
# user_id set that already passed the captcha (persist for the process lifetime)
captcha_passed: set = set()
CAPTCHA_TTL = 120  # seconds


@router.message(F.text.regexp(r"^\d{5}$"))
async def handle_captcha(message: types.Message):
    user_id = message.from_user.id
    entry = captcha_store.get(user_id)
    if entry is None:
        return  # not waiting for a captcha — let other handlers process

    code, expires = entry
    if time.time() > expires:
        del captcha_store[user_id]
        await message.answer("⏳ Капча устарела. Нажмите «Получить доступ» ещё раз.")
        return

    if message.text and message.text.strip() == code:
        del captcha_store[user_id]
        captcha_passed.add(user_id)
        await message.answer("✅ Капча пройдена! Получайте доступ:")
        await generate_vpn_key(message, None)
    else:
        # Wrong code — let them retry; refresh only on explicit request
        await message.answer("❌ Неверный код. Попробуйте ещё раз (код — 5 цифр с картинки).")

NOISE_RE = re.compile(r"^[\s.…,\-–—!?*+=_)*&^%#@~`'\"|\\/<>\[\]{}()0-9a-zA-Zа-яА-Я]{0,3}$")


def is_noise(text: str) -> bool:
    """Мусор, который НЕ должен попадать в поддержку и админу (22-09-26).

    Отсекаем: точки/тире/смайлы, «S», «1», «Впн», «Start», 5-значные коды капчи
    и код из прошлой сессии. Это не обращения — они засоряли support_messages
    и личку админа, из-за чего реальные жалобы терялись.
    """
    t = (text or "").strip()
    if not t:
        return True
    if len(t) <= 3 and not any(c in t for c in "?!"):
        return True                      # «.», «S», «1», «Лщ»
    if re.fullmatch(r"\d{4,8}", t):
        return True                      # коды капчи и прочие цифроблудия (393552, 025664)
    low = t.lower().strip("?.! ")
    if low in ("start", "старт", "начать", "интернет", "internet", "инет",
               "помогите", "помощь", "help", "включи", "подключи"):
        # короткий «клик по меню» — не обращение: юзер и так видит кнопки бота
        return True
    if low in ("впн", "vpn", "vpn?", "впн?", "хочу впн", "дай впн", "дай доступ",
               "нужен впн", "надо впн", "где впн"):
        # «хочу vpn» — это НЕ жалоба, но и не мусор: отдаём инструкцию (howto),
        # а не в поддержку. Обрабатывается ветвью howto в autoreply.
        return True
    return False


@router.message(F.text & ~F.text.startswith("/"))
async def support_fallback(message: types.Message):
    """Текстовое сообщение юзера = обращение в поддержку (кроме мусора и ссылок)."""
    text = (message.text or "").strip()
    if not text:
        return

    from autoreply import get_autoreply, looks_like_link

    auto = get_autoreply(message.from_user.id, text)   # 0 токенов, локальный матч

    # 0) мусор и «голые ссылки» в поддержку не пишем вообще: по ссылке отвечает
    #    автоответчик, мусор — это тапы/код капчи, админу они не нужны
    if is_noise(text) or looks_like_link(text):
        if auto:
            try:
                await message.answer(str(auto), parse_mode="HTML",
                                     disable_web_page_preview=True)
            except Exception:
                await message.answer(str(auto))
        return

    # 1) сохраняем в БД ВСЕГДА (история обращений нужна) — но с пометкой,
    #    если бот уже ответил сам: это отделяет «уже объяснили» от «нужен человек»
    try:
        db = get_db(message)
        await db.save_support_message(
            telegram_id=message.from_user.id,
            username=message.from_user.username or "",
            full_name=(message.from_user.full_name or ""),
            text=text,
        )
        if auto:
            await db.mark_support_auto_answered(message.from_user.id, text)
    except Exception as e:
        logger.warning(f"support save failed: {e}")

    # 2) пересылаем админу в личку — ТОЛЬКО если бот сам не ответил
    #    (жалоба «не работает» без совпадения или с истёкшим кулдауном = нужен человек)
    if auto:
        try:
            await message.answer(str(auto), parse_mode="HTML",
                                 disable_web_page_preview=True)
        except Exception:
            await message.answer(str(auto))
        return

    # 2.5) автоответа по правилам нет — пробуем ответить через внешний ИИ
    #      (ключ в .env). Нет ключа, ошибка, лимит или в ответе запрещённые слова —
    #      идём дальше как раньше: зовём человека. Поддержка не ломается никогда.
    _smart = None
    _sm_db = None
    try:
        from smart_reply import answer as _smart_answer, build_facts as _smart_facts
        _sm_db = get_db(message)
        _me = await _sm_db.get_user(message.from_user.id)
        _smart = _smart_answer(text, _smart_facts(_me))
    except Exception as e:
        logger.warning(f"smart reply failed: {e}")
        _smart = None
    if _smart:
        try:
            await message.answer(_smart, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await message.answer(_smart)
        try:
            await _sm_db.mark_support_auto_answered(message.from_user.id, text)
        except Exception:
            pass
        logger.info("smart reply sent (LLM)")
        return

    # 3) автоответа нет — сюда попадают только настоящие «нужен человек»

    try:
        uname = ("@" + message.from_user.username) if message.from_user.username else str(message.from_user.id)
        who = f"👤 {message.from_user.full_name} ({uname}, id {message.from_user.id})"
        await message.bot.send_message(
            ADMIN_ID,
            f"📩 Обращение\n{who}\n\n{text}",
        )
    except Exception as e:
        logger.warning(f"forward to admin failed: {e}")

    # 4) типового ответа нет — стандартная заглушка

    await message.answer(
        "✅ Обращение принято.\n\n"
        "Отвечу, как освобожусь — точный срок назвать не могу.\n\n"
        "Если проблема с подключением — обновите доступ в приложении "
        "(потяните список профилей вниз) и переподключитесь."
    )
