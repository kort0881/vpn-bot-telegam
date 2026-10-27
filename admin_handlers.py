"""
Admin handlers: stats, ban/unban, panel management.
"""
import logging
from datetime import datetime

from aiogram import types, Router, F
from aiogram.filters import Command

from config import ADMIN_ID, CHANNEL_ID
from database import Database
from logger import logger

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_only(handler):
    """Decorator to restrict handlers to admin only."""
    async def wrapper(message: types.Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Доступ запрещён.")
            return
        return await handler(message, *args, **kwargs)
    return wrapper


# ===================== ADMIN STATS =====================


@router.message(Command("admin"))
@admin_only
async def admin_cmd(message: types.Message):
    db: Database = message.bot.db
    users = await db.get_all_users()
    active = [u for u in users if u["is_active"] and not u["banned"]]
    banned = [u for u in users if u["banned"]]
    keys_count = await db.get_keys_count()

    text = (
        f"🔐 <b>Панель администратора</b>\n\n"
        f"👥 Всего пользователей: {len(users)}\n"
        f"✅ Активных: {len(active)}\n"
        f"⛔ Забанено: {len(banned)}\n"
        f"🔑 Всего выдано доступов: {keys_count}\n\n"
        f"<b>Команды:</b>\n"
        f"/stats — полная статистика\n"
        f"/ban [id] — забанить пользователя\n"
        f"/unban [id] — разбанить пользователя\n"
        f"/listusers — список всех пользователей\n"
    )
    await message.answer(text)


@router.message(Command("stats"))
@admin_only
async def stats_cmd(message: types.Message):
    db: Database = message.bot.db
    users = await db.get_all_users()
    active_users = [u for u in users if u["is_active"] and not u["banned"]]

    total_traffic = sum(u["used_bytes"] for u in users) / (1024**3)
    keys_count = await db.get_keys_count()

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {len(users)}\n"
        f"✅ Активных: {len(active_users)}\n"
        f"🔑 Выдано доступов: {keys_count}\n"
        f"📦 Использовано трафика: {total_traffic:.1f} ГБ\n"
        f"📢 Канал: {CHANNEL_ID or 'не указан'}"
    )
    await message.answer(text)


# ===================== BAN / UNBAN =====================


@router.message(Command("ban"))
@admin_only
async def ban_cmd(message: types.Message):
    db: Database = message.bot.db
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Укажите ID пользователя: /ban 123456789 [причина]\n"
            "Пример: /ban 123456789 не поддерживает проект"
        )
        return
    try:
        target_id = int(args[1])
        reason = " ".join(args[2:]).strip()
        user = await db.get_user(target_id)
        if not user:
            await message.answer(f"❌ Пользователь {target_id} не найден в БД.")
            return
        await db.ban_user(target_id, reason)
        _r = f" Причина: {reason}" if reason else " (причина не указана)"
        await message.answer(f"✅ Пользователь {target_id} ({user['username']}) забанен.{_r}")
        logger.info(f"Admin {message.from_user.id} banned user {target_id}: {reason or '-'}")
    except ValueError:
        await message.answer("❌ Неверный формат ID.")


@router.message(Command("unban"))
@admin_only
async def unban_cmd(message: types.Message):
    db: Database = message.bot.db
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажите ID пользователя: /unban 123456789")
        return
    try:
        target_id = int(args[1])
        await db.unban_user(target_id)
        await message.answer(f"✅ Пользователь {target_id} разбанен (причина бана стёрта).")
        logger.info(f"Admin {message.from_user.id} unbanned user {target_id}")
    except ValueError:
        await message.answer("❌ Неверный формат ID.")


# ===================== LIST USERS =====================


@router.message(Command("listusers"))
@admin_only
async def list_users_cmd(message: types.Message):
    db: Database = message.bot.db
    users = await db.get_all_users()
    if not users:
        await message.answer("❌ Нет пользователей.")
        return

    lines = []
    for u in users[:50]:  # Show top 50
        status = "✅" if u["is_active"] and not u["banned"] else "⛔" if u["banned"] else "⌛"
        used_gb = u["used_bytes"] / (1024**3)
        lines.append(f"{status} <code>{u['telegram_id']}</code> @{u['username']} — {used_gb:.1f} ГБ")

    text = "<b>Список пользователей</b>\n\n" + "\n".join(lines)
    if len(users) > 50:
        text += f"\n\n...и ещё {len(users) - 50} пользователей"

    await message.answer(text)


# ===================== DONATIONS (preserved from original) =====================

from aiogram.utils.keyboard import InlineKeyboardBuilder

DONATION_OPTIONS = {
    "coffee": {"amount": 50, "label": "☕️ Кофе", "description": "Угостите нас кофе ☕️"},
    "kvass": {"amount": 50, "label": "🥤 Квас", "description": "Угостите нас квасом 🥤"},
    "pizza": {"amount": 100, "label": "🍕 Пицца", "description": "Угостите нас пиццей 🍕"},
}


# ===================== ОПЛАТЫ (учёт заявок) =====================


@router.message(Command("payments"))
@admin_only
async def payments_cmd(message: types.Message, **kwargs):
    """Список последних заявок на чаевые."""
    db = message.bot.db
    rows = await db.get_payments(20)
    stats = await db.payments_stats()
    if not rows:
        await message.answer("💸 Заявок на чаевые пока нет.")
        return
    lines = [
        f"💸 <b>Чаевые</b>",
        f"Подтверждено: <b>{stats['total_confirmed']} чаевых</b> ({stats['cnt_confirmed']} шт), "
        f"ждут: <b>{stats['cnt_claimed']}</b>",
        "",
    ]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"] / 1000))
        mark = "✅" if r["status"] == "confirmed" else "🕐"
        who = f"@{r['username']}" if r.get("username") else (r.get("full_name") or "—")
        lines.append(f"{mark} <code>{r['id']}</code> {who} — {r['amount']} чаевых ({when})")
    lines.append("")
    lines.append("Подтвердить: <code>/paid ID</code>")
    await message.answer("\n".join(lines))


@router.message(Command("paid"))
@admin_only
async def paid_cmd(message: types.Message, **kwargs):
    """Подтвердить чаевые по id заявки."""
    db = message.bot.db
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/paid 12</code>")
        return
    pid = int(parts[1])
    pay = await db.get_payment(pid)
    if not pay:
        await message.answer(f"Заявки <code>{pid}</code> нет.")
        return
    if pay["status"] == "confirmed":
        await message.answer(f"Заявка <code>{pid}</code> уже подтверждена.")
        return
    ok = await db.confirm_payment(pid)
    if not ok:
        await message.answer("Не получилось подтвердить, попробуй ещё.")
        return
    await message.answer(
        f"✅ Подтверждено: <code>{pid}</code> — {pay['amount']} чаевых "
        f"(id {pay['telegram_id']})"
    )
    try:
        await message.bot.send_message(
            pay["telegram_id"],
            f"🫡 Спасибо, родина не забудет. Твои {pay['amount']} чаевых дошли, "
            f"всё зафиксировано.",
        )
    except Exception as e:
        logger.warning(f"pay confirm notify failed for {pay['telegram_id']}: {e}")


# ===================== ЖАЛОБЫ / ОБРАЩЕНИЯ =====================


@router.message(Command("complaints"))
@admin_only
async def complaints_cmd(message: types.Message):
    db: Database = message.bot.db
    msgs = await db.get_support_messages(limit=20)
    if not msgs:
        await message.answer("Обращений нет.")
        return
    new_cnt = await db.count_new_support()
    parts = [f"📩 <b>Обращения</b> (новых: {new_cnt})"]
    for m in msgs:
        ts = datetime.fromtimestamp(m["created_at"] / 1000).strftime("%d.%m %H:%M")
        uname = ("@" + m["username"]) if m.get("username") else str(m.get("telegram_id"))
        mark = "🆕" if m.get("status") == "new" else "✅"
        txt = (m.get("text") or "")[:150]
        parts.append(f"{mark} #{m['id']} | {ts} | {uname}\n{txt}")
    await message.answer("\n\n".join(parts), parse_mode="HTML")


@router.message(Command("resolved"))
@admin_only
async def resolved_cmd(message: types.Message):
    args = (message.text or "").split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /resolved <id>")
        return
    ok = await message.bot.db.resolve_support_message(int(args[1]))
    await message.answer("✅ Помечено обработанным" if ok else "❌ Не удалось")
