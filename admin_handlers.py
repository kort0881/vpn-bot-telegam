import asyncio
from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from logger import logger
from config import DATA_FILE, ADMIN_ID

from settings import (
    get_banned_users,
    add_user_to_banned,
    remove_user_from_banned,
    toggle_subscription_check,
    toggle_logs_enabled,
    toggle_captcha_enabled,
)

router = Router()

# ===================== ДОНАТЫ СО ЗВЁЗДАМИ =====================

DONATION_OPTIONS = {
    "small": {"amount": 50, "label": "☕ Кофе", "description": "Небольшая поддержка"},
    "medium": {"amount": 100, "label": "🍕 Пицца", "description": "Средняя поддержка"},
    "large": {"amount": 250, "label": "🎁 Подарок", "description": "Щедрая поддержка"},
    "huge": {"amount": 500, "label": "🚀 Ракета", "description": "Огромная поддержка"},
}


@router.message(Command("donate"))
async def donate_cmd(message: types.Message):
    """Показывает меню донатов"""
    builder = InlineKeyboardBuilder()
    
    for key, option in DONATION_OPTIONS.items():
        builder.button(
            text=f"{option['label']} - {option['amount']} ⭐",
            callback_data=f"donate:{key}"
        )
    
    builder.button(text="💫 Свою сумму", callback_data="donate:custom")
    builder.adjust(2)
    
    await message.answer(
        "⭐ <b>Поддержать проект</b>\n\n"
        "Выберите сумму доната в звёздах Telegram.\n"
        "Все средства идут на развитие бота!\n\n"
        "💡 <i>Звёзды можно купить в настройках Telegram</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("donate:"))
async def donate_callback(callback: types.CallbackQuery):
    """Обработка выбора суммы доната"""
    action = callback.data.split(":")[1]
    
    if action == "custom":
        await callback.message.edit_text(
            "💫 <b>Произвольная сумма</b>\n\n"
            "Отправьте команду с нужной суммой:\n"
            "de>/donate_custom 100</code>\n\n"
            "Минимум: 1 ⭐",
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    if action not in DONATION_OPTIONS:
        await callback.answer("Неизвестная опция", show_alert=True)
        return
    
    option = DONATION_OPTIONS[action]
    await send_donation_invoice(
        callback.message, 
        callback.from_user.id,
        option["amount"], 
        option["label"],
        option["description"]
    )
    await callback.answer()


@router.message(Command("donate_custom"))
async def donate_custom_cmd(message: types.Message):
    """Донат произвольной суммы"""
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer(
            "Укажите сумму в звёздах:\n"
            "de>/donate_custom 100</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        amount = int(args[1])
        if amount < 1:
            await message.answer("Минимальная сумма: 1 ⭐")
            return
        if amount > 10000:
            await message.answer("Максимальная сумма: 10000 ⭐")
            return
            
        await send_donation_invoice(
            message,
            message.from_user.id,
            amount,
            f"💫 Донат {amount} ⭐",
            "Произвольная сумма поддержки"
        )
    except ValueError:
        await message.answer("Сумма должна быть числом")


async def send_donation_invoice(
    message: types.Message, 
    user_id: int, 
    amount: int, 
    title: str, 
    description: str
):
    """Отправляет счёт на оплату звёздами"""
    try:
        await message.answer_invoice(
            title=title,
            description=description,
            payload=f"donation_{user_id}_{amount}",
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=amount)],
        )
        logger.info(f"Invoice sent to user {user_id} for {amount} stars")
    except Exception as e:
        logger.error(f"Failed to send invoice: {e}")
        await message.answer(
            "❌ Не удалось создать счёт.\n"
            "Убедитесь, что у бота включены платежи.",
            parse_mode="HTML"
        )


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Подтверждение платежа"""
    await pre_checkout_query.answer(ok=True)
    logger.info(f"Pre-checkout approved for {pre_checkout_query.from_user.id}")


@router.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    """Обработка успешного платежа"""
    payment = message.successful_payment
    user_id = message.from_user.id
    amount = payment.total_amount
    payload = payment.invoice_payload
    
    logger.info(
        f"Successful payment: user={user_id}, "
        f"amount={amount} stars, payload={payload}"
    )
    
    await message.answer(
        f"🎉 <b>Спасибо за донат!</b>\n\n"
        f"Вы отправили: <b>{amount} ⭐</b>\n\n"
        f"Ваша поддержка очень важна для развития проекта! 💜",
        parse_mode="HTML"
    )
    
    try:
        user = message.from_user
        username = f"@{user.username}" if user.username else "без username"
        await message.bot.send_message(
            ADMIN_ID,
            f"💰 <b>Новый донат!</b>\n\n"
            f"От: {user.full_name} ({username})\n"
            f"ID: de>{user_id}</code>\n"
            f"Сумма: <b>{amount} ⭐</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin about donation: {e}")


@router.message(Command("refund"))
async def refund_cmd(message: types.Message):
    """Возврат средств (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("faq")
        return
    
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "Использование:\n"
            "de>/refund [user_id] [telegram_payment_charge_id]</code>",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(args[1])
        charge_id = args[2]
        
        await message.bot.refund_star_payment(
            user_id=user_id,
            telegram_payment_charge_id=charge_id
        )
        await message.answer(f"✅ Возврат выполнен для пользователя {user_id}")
        logger.info(f"Refund processed for user {user_id}, charge_id={charge_id}")
    except Exception as e:
        await message.answer(f"❌ Ошибка возврата: {e}")
        logger.error(f"Refund failed: {e}")


@router.message(Command("donations"))
async def donations_stats_cmd(message: types.Message):
    """Статистика донатов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("faq")
        return
    
    await message.answer(
        "📊 <b>Статистика донатов</b>\n\n"
        "<i>Для полной статистики добавьте сохранение в базу данных</i>",
        parse_mode="HTML"
    )


@router.message(Command("admin"))
async def admin_cmd(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"/admin, user_id={user_id}, admin_id={ADMIN_ID}")

    if user_id != ADMIN_ID:
        logger.warning(f"access denied, user_id={user_id}")
        await message.answer("faq")
        return

    await message.answer(
        "<b>🔧 Админ-панель</b>\n\n"
        "<b>Пользователи:</b>\n"
        "/ban [id] - забанить\n"
        "/unban [id] - разбанить\n"
        "/ban_list - список забаненных\n\n"
        "<b>Настройки:</b>\n"
        "/sub - вкл/выкл проверку подписки\n"
        "/logs - вкл/выкл логи\n"
        "/captcha - вкл/выкл капчу\n\n"
        "<b>Донаты:</b>\n"
        "/donations - статистика\n"
        "/refund [user_id] [charge_id] - возврат",
        parse_mode="HTML"
    )


@router.message(Command("ban"))
async def ban_cmd(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    logger.info(f"/ban, user_id={user_id}, args={args}")

    if user_id != ADMIN_ID:
        logger.warning(f"ban attempt without permissions, user_id={user_id}")
        await message.answer("faq")
        return

    if len(args) < 2:
        await message.answer("/ban [id]")
        return

    try:
        ban_id = int(args[1])
        logger.info(f"ban attempt, ban_id={ban_id}")

        success = await add_user_to_banned(ban_id, DATA_FILE)
        if success:
            logger.info(f"banned, ban_id={ban_id}")
            await message.answer(f"{ban_id} banned")
        else:
            logger.info(f"already on the list, ban_id={ban_id}")
            await message.answer(f"{ban_id} already on the list")

    except ValueError:
        await message.answer("id must be a number")


@router.message(Command("unban"))
async def unban_cmd(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)

    if user_id != ADMIN_ID:
        await message.answer("faq")
        return

    if len(args) < 2:
        await message.answer("/unban [id]")
        return

    try:
        unban_id = int(args[1])
        success = await remove_user_from_banned(unban_id, DATA_FILE)
        if success:
            await message.answer(f"{unban_id} unbanned")
        else:
            await message.answer(f"{unban_id} not found in the list")
    except ValueError:
        await message.answer("id must be a number")


@router.message(Command("ban_list"))
async def ban_list_cmd(message: types.Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("faq")
        return

    banned_users = await get_banned_users(DATA_FILE)
    if banned_users:
        formatted_list = "\n".join([f"{uid}" for uid in banned_users])
        await message.answer(
            f"list of banned ({len(banned_users)}):\n\n{formatted_list}"
        )
    else:
        await message.answer("the banned list is empty")


@router.message(Command("logs"))
async def logs_toggle_cmd(message: types.Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("faq")
        return

    success, new_state = await toggle_logs_enabled()

    if success:
        status = "on" if new_state else "off"
        await message.answer(f"logs are now de>{status}</code>", parse_mode="HTML")
        logger.info(f"logs toggled to {new_state} by admin")
    else:
        await message.answer("error when switching logs")


@router.message(Command("sub"))
async def sub_toggle_cmd(message: types.Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("faq")
        return

    success, new_state = await toggle_subscription_check()

    if success:
        status = "on" if new_state else "off"
        await message.answer(
            f"subscription verification now de>{status}</code>", parse_mode="HTML"
        )
        logger.info(f"subscription check toggled to {new_state} by admin")
    else:
        await message.answer("error when switching subscription check")


@router.message(Command("captcha"))
async def captcha_toggle_cmd(message: types.Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        await message.answer("faq")
        return

    success, new_state = await toggle_captcha_enabled()

    if success:
        status = "on" if new_state else "off"
        await message.answer(
            f"captcha are now de>{status}</code>", parse_mode="HTML"
        )
        logger.info(f"captcha toggled to {new_state} by admin")
    else:
        await message.answer("error when switching captcha")
