#!/usr/bin/env python3
"""Патч 4: авто-снятие бонуса при отписке от закрытого канала (main.py).
Логика: если у юзера есть VIP-клиент (insider_<id>), а подписки на закрытый
канал нет -> выключаем VIP-клиента. Базовый ключ НЕ трогаем.
Идемпотентно.
"""
BASE = "/opt/vpn-bot-telegam"


def patch_main():
    p = f"{BASE}/main.py"
    s = open(p, encoding="utf-8").read()
    if "vip_sweep" in s or "set_vip_enabled" in s:
        print("main.py: уже пропатчен")
        return

    # импорт check_vip_subscription
    s = s.replace(
        "from logger import logger, check_subscription",
        "from logger import logger, check_subscription, check_vip_subscription",
        1,
    )

    # добавим блок проверки VIP после обработки каждого юзера.
    # Вставляем внутрь for-цикла, после блока renew/disable (перед "except Exception as e:"
    # который ловит ошибку на юзера).
    anchor = """                except Exception as e:
                    logger.error(
                        f"Renewal error for user {u.get('telegram_id')}: {e}"
                    )"""
    add = """                    # --- VIP бонус: снять, если вышел из закрытого канала ---
                    try:
                        await vip_sweep_one(bot, panel, u)
                    except Exception as e:
                        logger.error(
                            f"VIP sweep failed for {u.get('telegram_id')}: {e}"
                        )
""" + anchor
    assert anchor in s, "main.py: якорь не найден"
    s = s.replace(anchor, add, 1)

    # сама функция vip_sweep_one + константы — перед renewal_loop
    anchor2 = "async def renewal_loop(bot: Bot, db: Database, panel: PanelSession):"
    fn = '''# ================= VIP бонус: авто-снятие =================
# Бонусный канал живёт, пока юзер в закрытом канале. Вышел -> VIP-клиент
# выключается (базовый ключ не трогаем). Выдача/восстановление — по кнопке.

from config import VIP_CHANNEL_ID as _VIP_CH, VIP_INBOUND_ID as _VIP_IB

_VIP_SWEEP_LAST: dict = {}   # telegram_id -> ts последней проверки
VIP_SWEEP_MIN_INTERVAL = 30 * 60   # не чаще раза в 30 минут на юзера


async def vip_sweep_one(bot: Bot, panel: PanelSession, u: dict):
    """Проверить подписку на закрытый канал; если нет — выключить VIP-клиента."""
    if not _VIP_CH:
        return
    tg_id = u.get("telegram_id")
    if not tg_id:
        return
    now = time.time()
    if now - _VIP_SWEEP_LAST.get(tg_id, 0) < VIP_SWEEP_MIN_INTERVAL:
        return
    _VIP_SWEEP_LAST[tg_id] = now

    # своего клиента нет — нечего проверять (дёшево и без лишних API-вызовов)
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
                    "🎁 Ваш бонусный канал отключён — вы вышли из закрытого канала.\\n"
                    "Вернётесь — нажмите «🎁 БОНУС КАНАЛ» и получите снова.",
                )
            except Exception as e:
                logger.error(f"VIP revoke notify failed for {tg_id}: {e}")


# ==========================================================

'''
    assert anchor2 in s, "main.py: якорь 2 не найден"
    s = s.replace(anchor2, fn + anchor2, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("main.py: OK")


if __name__ == "__main__":
    patch_main()
    print("Патч 4 применён.")
