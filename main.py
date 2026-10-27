"""
Main bot entry point.
Initializes DB, panel session, starts polling and the renewal loop.
"""
import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    EXPIRE_DAYS,
    RENEW_INTERVAL_MINUTES,
    RENEW_THRESHOLD_DAYS,
    PANEL_RU_URL,
    PANEL_RU_API_TOKEN,
    RU_INBOUND_IDS,
)
from database import Database
from panel_api import PanelSession
from user_handlers import router as user_router
from admin_handlers import router as admin_router
from callbacks import router as callbacks_router
from inline import router as inline_router
from poll_watch import router as poll_router
from survey import router as survey_router
from logger import logger, check_subscription, check_vip_subscription

# Global instances (injected into handlers via bot data)
db = Database()
panel = PanelSession()
panel_ru = PanelSession(
    base_url=PANEL_RU_URL,
    api_token=PANEL_RU_API_TOKEN,
    inbound_ids=RU_INBOUND_IDS,
)
# Per-user lock: prevents double-click creating duplicate clients in panels.
user_locks: dict = {}

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()

TRAFFIC_SYNC_MINUTES = 15  # как часто перечитывать трафик с RU-панели
RENEW_MS = EXPIRE_DAYS * 24 * 3600 * 1000
THRESHOLD_MS = RENEW_THRESHOLD_DAYS * 24 * 3600 * 1000

KICK_TEXT = (
    "😔 Жаль, брат, что доступ не помог ёптить.\n"
    "Ты отписался от @vlesstrojan — доступ отключён.\n"
    "Новый доступ можно будет получить через 7 дней."
)


# ================= VIP бонус: авто-снятие =================
# Бонусный канал живёт, пока юзер в закрытом канале. Вышел -> VIP-клиент
# выключается (базовый доступ не трогаем). Выдача/восстановление — по кнопке.

from config import VIP_CHANNEL_ID as _VIP_CH, VIP_INBOUND_ID as _VIP_IB

_VIP_SWEEP_LAST: dict = {}   # telegram_id -> ts последней проверки
VIP_SWEEP_MIN_INTERVAL = 30 * 60   # не чаще раза в 30 минут на юзера


async def vip_sweep_one(bot: Bot, panel: PanelSession, u: dict):
    """Проверить подписку на закрытый канал; если нет — выключить VIP-приложения."""
    if not _VIP_CH:
        return
    tg_id = u.get("telegram_id")
    if not tg_id:
        return
    now = time.time()
    if now - _VIP_SWEEP_LAST.get(tg_id, 0) < VIP_SWEEP_MIN_INTERVAL:
        return
    _VIP_SWEEP_LAST[tg_id] = now

    # своего приложения нет — нечего проверять (дёшево и без лишних API-вызовов)
    existing_uuid = await panel._get_client_uuid(f"uv{tg_id}")
    if not existing_uuid:
        return

    is_sub = await check_vip_subscription(bot, tg_id)
    if not is_sub:
        ok = await panel.set_vip_enabled(tg_id, False)
        if ok:
            logger.info(f"VIP bonus revoked for {tg_id} (left the closed channel)")
            try:
                await bot.send_message(
                    tg_id,
                    "🎁 Ваш бонусный канал отключён — вы вышли из закрытого канала.\n"
                    "Вернётесь — нажмите «🎁 БОНУС КАНАЛ» и получите снова.",
                )
            except Exception as e:
                logger.error(f"VIP revoke notify failed for {tg_id}: {e}")


# ==========================================================

REPORT_PERIOD_MS = 30 * 24 * 3600 * 1000  # раз в 30 дней  (monthly_report_v1)


def _fmt_gb(b: float) -> str:
    return f"{b / (1024 ** 3):.1f} ГБ"


async def monthly_report_loop(bot: Bot, db, panel, panel_ru):
    """Раз в сутки проверяет, кому пора отчёт (размазано по дате регистрации).
    Отчёт выглядит как сервисная статистика, со строкой поддержки в конце.
    """
    import datetime as _dt
    await asyncio.sleep(600)  # не в первую секунду старта
    while True:
        try:
            now_ms = int(time.time() * 1000)
            due = await db.users_due_for_report(REPORT_PERIOD_MS)
            logger.info(f"monthly report: {len(due)} юзеров к отправке")
            sent = 0
            for u in due:
                tg = u["telegram_id"]
                try:
                    # трафик: тянем из RU-панели, иначе из финки
                    used = u.get("used_bytes") or 0.0
                    for p in (panel_ru, panel):
                        try:
                            tr = await p.get_client_traffic(u["email"])
                            if tr:
                                u_bytes = tr.get("up", 0) + tr.get("down", 0)
                                if u_bytes:
                                    used = float(u_bytes)
                                    try:
                                        await db.update_user_traffic(tg, used)
                                    except Exception:
                                        pass
                                break
                        except Exception:
                            continue

                    expiry_date = _dt.datetime.fromtimestamp(
                        (u["expiry_at"] or 0) / 1000
                    ).strftime("%d.%m.%Y")
                    total_gb = (u.get("total_bytes") or 0) / (1024 ** 3)

                    text = (
                        "📊 <b>Ваш месяц в цифрах</b>\n\n"
                        f"📈 Использовано: <b>{_fmt_gb(used)}</b> из {total_gb:.0f} ГБ\n"
                        f"📅 Доступ активна до: <b>{expiry_date}</b>\n"
                        "🔑 Канал в работе, сбоев не зафиксировано\n\n"
                        "Спасибо, что с нами. Если удобно — "
                        "«💵 Поддержать проект»."
                    )
                    from aiogram.utils.keyboard import InlineKeyboardBuilder as _IKB
                    kb = _IKB()
                    kb.button(text="🔑 МОЙ ДОСТУП", callback_data="my_key")
                    kb.button(text="💵 Поддержать проект", callback_data="donate")
                    kb.adjust(1)
                    await bot.send_message(tg, text, reply_markup=kb.as_markup())
                    await db.mark_report_sent(tg, now_ms)
                    sent += 1
                    await asyncio.sleep(1.0)  # мягкий темп, не спамим API
                except Exception as e:
                    logger.error(f"report send failed for {tg}: {e}")
            logger.info(f"monthly report done: sent={sent}/{len(due)}")
        except Exception as e:
            logger.error(f"monthly report loop error: {e}")
        await asyncio.sleep(24 * 3600)


async def renewal_loop(bot: Bot, db: Database, panel: PanelSession):
    """
    Periodic subscription check:
    - subscribed users with < threshold days left  -> renew +EXPIRE_DAYS
    - unsubscribed users                           -> disable client, notify
    """
    logger.info(
        f"Renewal loop started (interval={RENEW_INTERVAL_MINUTES}m, "
        f"threshold={RENEW_THRESHOLD_DAYS}d, renew={EXPIRE_DAYS}d)"
    )
    while True:
        try:
            users = await db.get_active_users()
            now = int(time.time() * 1000)
            for u in users:
                try:
                    subscribed = await check_subscription(
                        bot, u["telegram_id"], CHANNEL_ID
                    )
                    expiry = u["expiry_at"] or 0
                    if subscribed:
                        if expiry - now < THRESHOLD_MS:
                            new_expiry = max(expiry, now) + RENEW_MS
                            # С 15-08-26 новые юзеры существуют ТОЛЬКО на RU-панели
                            # (анти-палёж: финские доступы новым не выдаём). Финская
                            # панель — только для старых юзеров. Продлеваем там,
                            # где приложение реально есть.
                            ok_fin = await panel.update_client(
                                u["email"], u["client_uuid"],
                                enable=True, expiry_time=new_expiry,
                            )
                            ok_ru = False
                            try:
                                ok_ru = bool(await panel_ru.update_client(
                                    u["email"], u["client_uuid"],
                                    enable=True, expiry_time=new_expiry,
                                ))
                            except Exception as e:
                                logger.error(f"RU panel renew failed for {u['email']}: {e}")
                            if ok_ru:
                                await db.update_user_expiry(u["telegram_id"], new_expiry)
                                logger.info(
                                    f"Renewed {u['email']} -> {new_expiry} "
                                    f"(subscribed, {expiry - now:.0f} ms left)"
                                )
                    else:
                        ok_fin = await panel.update_client(
                            u["email"], u["client_uuid"], enable=False
                        )
                        ok_ru = False
                        try:
                            ok_ru = bool(await panel_ru.update_client(
                                u["email"], u["client_uuid"], enable=False
                            ))
                        except Exception as e:
                            logger.error(f"RU panel disable failed for {u['email']}: {e}")
                        # RU — реальный ВХОД каскада, ключ живёт ТАМ; финка (not found => True)
                        # бан не подтверждает. Баг 09-10-26: 33 отписавшихся
                        # остались с живым ключом на RU. Требуем ok_ru.
                        if ok_ru:
                            await db.set_user_active(u["telegram_id"], False)
                            await db.set_unsubscribed_at(u["telegram_id"], int(time.time() * 1000))
                            logger.info(f"Disabled {u['email']} (unsubscribed)" + ("" if u.get("unsubscribed_at") else ", resubscribe ban set"))
                            try:
                                await bot.send_message(u["telegram_id"], KICK_TEXT)
                            except Exception as e:
                                logger.error(
                                    f"Kick message failed for {u['telegram_id']}: {e}"
                                )
                    # --- VIP бонус: снять, если вышел из закрытого канала ---
                    try:
                        await vip_sweep_one(bot, panel, u)
                    except Exception as e:
                        logger.error(
                            f"VIP sweep failed for {u.get('telegram_id')}: {e}"
                        )
                except Exception as e:
                    logger.error(
                        f"Renewal error for user {u.get('telegram_id')}: {e}"
                    )
        except Exception as e:
            logger.error(f"Renewal loop error: {e}")
        await asyncio.sleep(RENEW_INTERVAL_MINUTES * 60)


async def traffic_sync_loop(db, panel_ru):
    """Раз в TRAFFIC_SYNC_MINUTES тянет трафик ВСЕХ клиентов с RU-панели ОДНИМ
    запросом и обновляет used_bytes.

    Раньше used_bytes обновлялся ТОЛЬКО в месячном отчёте (и лишь у юзеров,
    которым он «пришёл»), поэтому счётчик в боте врал в разы (12-10-26:
    показывал 29.8 GiB при реальных 83.7) и лимит 300 ГБ ничего не отражал.
    """
    while True:
        try:
            m = await panel_ru.get_traffic_map()
            n = await db.bulk_update_traffic(m)
            logger.info(f"traffic sync: {len(m)} клиентов в панели, {n} строк обновлено")
        except Exception as e:
            logger.error(f"traffic sync failed: {e}")
        await asyncio.sleep(TRAFFIC_SYNC_MINUTES * 60)


async def heartbeat_loop():
    """
    Periodic heartbeat so the watchdog's log-activity check sees fresh mtime
    even when the bot is idle (no user traffic = no log writes = watchdog
    used to bounce a perfectly healthy bot every ~15 min).
    """
    while True:
        await asyncio.sleep(5 * 60)
        logger.info("heartbeat — bot alive")


async def on_startup():
    """Called when bot starts."""
    await db.connect()
    # Panel session will auth lazily on first use
    logger.info("Bot started — DB connected, panel session ready")
    asyncio.create_task(renewal_loop(bot, db, panel))
    asyncio.create_task(monthly_report_loop(bot, db, panel, panel_ru))
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(traffic_sync_loop(db, panel_ru))


async def on_shutdown():
    """Called when bot stops."""
    await db.close()
    await panel.close()
    await panel_ru.close()
    logger.info("Bot stopped — connections closed")


async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    dp.include_router(admin_router)
    dp.include_router(callbacks_router)
    dp.include_router(user_router)
    import tt_handlers  # noqa: E402,F401
    dp.include_router(inline_router)
    dp.include_router(survey_router)
    dp.include_router(poll_router)

    # Inject shared instances into bot data for handlers
    bot.db = db
    bot.panel = panel
    bot.panel_ru = panel_ru
    bot.user_locks = user_locks

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
