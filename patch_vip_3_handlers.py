#!/usr/bin/env python3
"""Патч 3: кнопка «БОНУС КАНАЛ» + дисклеймер + авто-снятие бонуса.
Идемпотентно. Запускать после патчей 1 и 2.
"""
BASE = "/opt/vpn-bot-telegam"


def patch_user_handlers():
    p = f"{BASE}/user_handlers.py"
    s = open(p, encoding="utf-8").read()
    if "vip_bonus" in s:
        print("user_handlers.py: уже пропатчен")
        return

    # 1. импорты
    s = s.replace(
        "from logger import logger, check_subscription, send_logs",
        "from logger import logger, check_subscription, check_vip_subscription, send_logs",
        1,
    )
    # VIP-константы в импорт из config
    if "from config import" in s:
        # найдём строку импорта config и добавим VIP_* в конец
        import re as _re
        m = _re.search(r"from config import \(([^)]*)\)", s)
        if m and "VIP_CHANNEL_ID" not in m.group(1):
            block = m.group(0)
            inner = m.group(1).rstrip()
            new = f"from config import ({inner}\n    VIP_CHANNEL_ID,\n    VIP_INVITE_URL,\n    VIP_TOTAL_BYTES,\n)"
            s = s.replace(block, new, 1)
        else:
            m2 = _re.search(r"from config import ([^\n(]+)", s)
            if m2 and "VIP_CHANNEL_ID" not in m2.group(1):
                s = s.replace(m2.group(0),
                              m2.group(0) + ", VIP_CHANNEL_ID, VIP_INVITE_URL, VIP_TOTAL_BYTES", 1)

    # 2. тексты + клавиатура + обработчики (перед section START)
    anchor = "# ===================== START ====================="
    add = '''# ===================== VIP БОНУС =====================

VIP_DISCLAIMER = (
    "🎁 <b>Бонусный канал</b>\\n\\n"
    "Закрытый канал — {link}\\n\\n"
    "Подписчикам бонусом ещё один канал до момента отписки."
)


def vip_kb(is_sub: bool = False) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if is_sub:
        builder.button(text="🎁 ВЫДАТЬ БОНУС КАНАЛ", callback_data="vip_bonus")
    else:
        builder.button(text="📢 ЗАКРЫТЫЙ КАНАЛ", url=VIP_INVITE_URL or "#")
        builder.button(text="🎁 ВЫДАТЬ БОНУС КАНАЛ", callback_data="vip_bonus")
    builder.adjust(1)
    return builder


@router.callback_query(F.data == "vip_info")
async def vip_info_callback(callback: types.CallbackQuery):
    """Показать дисклеймер про бонус."""
    await callback.answer()
    sub = await check_vip_subscription(callback.bot, callback.from_user.id)
    try:
        await callback.message.edit_text(
            VIP_DISCLAIMER.format(link=VIP_INVITE_URL or "#"),
            reply_markup=vip_kb(sub).as_markup(),
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except Exception:
        await callback.message.answer(
            VIP_DISCLAIMER.format(link=VIP_INVITE_URL or "#"),
            reply_markup=vip_kb(sub).as_markup(),
            parse_mode="HTML", disable_web_page_preview=True,
        )


@router.callback_query(F.data == "vip_bonus")
async def vip_bonus_callback(callback: types.CallbackQuery):
    """Выдать бонусный канал: проверка подписки на закрытый канал -> выдача."""
    await callback.answer()
    user_id = callback.from_user.id
    bot = callback.bot

    # 1. подписка на закрытый канал (fail-closed)
    is_sub = await check_vip_subscription(bot, user_id)
    if not is_sub:
        txt = (
            "🚫 Друг, тебя нет на закрытом канале.\\n\\n"
            f"Подпишись здесь: {VIP_INVITE_URL or '#'}\\n\\n"
            "Потом вернись и нажми кнопку снова."
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="📢 ПОДПИСАТЬСЯ", url=VIP_INVITE_URL or "#")
        kb.button(text="🎁 Я ПОДПИСАЛСЯ", callback_data="vip_bonus")
        kb.adjust(1)
        try:
            await callback.message.edit_text(txt, reply_markup=kb.as_markup(),
                                             disable_web_page_preview=True)
        except Exception:
            await callback.message.answer(txt, reply_markup=kb.as_markup(),
                                          disable_web_page_preview=True)
        await send_logs(message=callback.message, log="VIP: не подписан", user_id=user_id)
        return

    # 2. выдать/переиспользовать VIP-клиента
    panel = get_panel(callback.message)
    panel_ru = get_panel_ru(callback.message)
    try:
        res = await panel.add_vip_client(user_id)
    except Exception as e:
        logger.error(f"VIP issue failed for {user_id}: {e}")
        res = None

    if not res:
        await callback.message.answer("⚠️ Не удалось выдать бонус. Попробуйте позже.")
        await send_logs(message=callback.message, log=f"VIP: ошибка выдачи", user_id=user_id)
        return

    gb = VIP_TOTAL_BYTES / (1024 ** 3)
    txt = (
        "🎁 <b>Бонусный канал выдан!</b>\\n\\n"
        "Ваша отдельная ссылка (добавьте её в клиент отдельным профилем):\\n\\n"
        f"<code>{res['link']}</code>\\n\\n"
        f"📊 Лимит: {gb:.0f} ГБ\\n"
        "🔓 Работает, пока вы в закрытом канале."
    )
    await callback.message.answer(txt, parse_mode="HTML", disable_web_page_preview=True)
    await send_logs(message=callback.message, log="VIP: бонус выдан", user_id=user_id)


# =====================================================

'''
    assert anchor in s, "user_handlers.py: якорь не найден (section START)"
    s = s.replace(anchor, add + anchor, 1)

    # 3. кнопка в главном меню
    s = s.replace(
        '''    builder.button(text="📖 ИНСТРУКЦИЯ", callback_data="help")
    builder.button(text="💸 ДОНАТ", callback_data="donate")
    builder.adjust(1)

    await message.answer(
        start_text,''',
        '''    builder.button(text="🎁 БОНУС КАНАЛ", callback_data="vip_info")
    builder.button(text="📖 ИНСТРУКЦИЯ", callback_data="help")
    builder.button(text="💸 ДОНАТ", callback_data="donate")
    builder.adjust(1)

    await message.answer(
        start_text,''',
        1,
    )
    open(p, "w", encoding="utf-8").write(s)
    print("user_handlers.py: OK")


if __name__ == "__main__":
    patch_user_handlers()
    print("Патч 3 применён.")
