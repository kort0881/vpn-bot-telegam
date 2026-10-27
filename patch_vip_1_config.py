#!/usr/bin/env python3
"""Патч бота: добавляет VIP (бонусный канал для подписчиков закрытого канала).
Идемпотентно: повторный запуск ничего не дублирует.
Бэкапы уже сделаны (.bak_pre_vip_*). Обратный откат — restore_backup.sh.
"""
import re, sys, os, shutil, time

BASE = "/opt/vpn-bot-telegam"
STAMP = time.strftime("%Y-%m-%d_%H%M%S")


def patch_config():
    p = f"{BASE}/config.py"
    s = open(p, encoding="utf-8").read()
    if "VIP_CHANNEL_ID" in s:
        print("config.py: уже пропатчен, пропускаем")
        return
    anchor = "# Convert units"
    add = '''# --- VIP / бонусный канал (закрытый канал -> доп. канал на финке) ---
# 16-09-26: подписчикам закрытого канала бонусом выдаётся отдельная ссылка
# на бонусный вход (финка, порт 2096, WS+TLS, path /bonus-ws).
VIP_CHANNEL_ID: str = getenv("VIP_CHANNEL_ID") or ""
VIP_INVITE_URL: str = getenv("VIP_INVITE_URL") or ""
VIP_INBOUND_ID: int = int(getenv("VIP_INBOUND_ID", "8"))
VIP_PORT: int = int(getenv("VIP_PORT", "2096"))
VIP_PATH: str = getenv("VIP_PATH", "/bonus-ws")
VIP_HOST: str = getenv("VIP_HOST", "dostupnety.website")
VIP_SNI: str = getenv("VIP_SNI", "dostupnety.website")
VIP_SERVER: str = getenv("VIP_SERVER", "179.198.50.180")
VIP_TOTAL_BYTES: int = int(getenv("VIP_TOTAL_GB", "300")) * 1024 * 1024 * 1024

'''
    assert anchor in s, "config.py: якорь не найден"
    s = s.replace(anchor, add + anchor, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("config.py: OK")


def patch_env():
    p = f"{BASE}/.env"
    s = open(p, encoding="utf-8").read()
    if "VIP_CHANNEL_ID" in s:
        print(".env: уже пропатчен, пропускаем")
        return
    add = (f"\n# VIP / бонусный канал (16-09-26)\n"
           f"VIP_CHANNEL_ID=-1002926041814\n"
           f"VIP_INVITE_URL=https://t.me/+EdEfIkn83Wg3ZTE6\n"
           f"VIP_INBOUND_ID=8\n"
           f"VIP_PORT=2096\n"
           f"VIP_PATH=/bonus-ws\n"
           f"VIP_HOST=dostupnety.website\n"
           f"VIP_SNI=dostupnety.website\n"
           f"VIP_SERVER=179.198.50.180\n"
           f"VIP_TOTAL_GB=300\n")
    open(p, "a", encoding="utf-8").write(add)
    os.chmod(p, 0o600)
    print(".env: OK")


def patch_logger():
    p = f"{BASE}/logger.py"
    s = open(p, encoding="utf-8").read()
    if "check_vip_subscription" in s:
        print("logger.py: уже пропатчен, пропускаем")
        return
    s = s.replace(
        "from config import CHANNEL_ID, ADMIN_ID, LOG_DIR",
        "from config import CHANNEL_ID, ADMIN_ID, LOG_DIR, VIP_CHANNEL_ID",
        1,
    )
    anchor = "# ---- Admin log notifications ----"
    add = '''async def check_vip_subscription(bot: Bot, user_id: int) -> bool:
    """
    Проверка подписки на ЗАКРЫТЫЙ (VIP) канал.
    ВАЖНО: fail-closed — в отличие от check_subscription, при любой ошибке
    возвращаем False (бонус не выдаём), т.к. бонус — привилегия, а не право.
    """
    if not VIP_CHANNEL_ID:
        logger.warning("VIP_CHANNEL_ID not configured — VIP bonus disabled")
        return False
    try:
        chat_member = await bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id)
        if chat_member.status in {"member", "administrator", "creator"}:
            return True
        if chat_member.status == "restricted":
            return bool(chat_member.is_member)
        return False
    except TelegramBadRequest as e:
        logger.warning(f"VIP check failed for {user_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"VIP check error for {user_id}: {e}")
        return False


'''
    assert anchor in s, "logger.py: якорь не найден"
    s = s.replace(anchor, add + anchor, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("logger.py: OK")


if __name__ == "__main__":
    patch_config()
    patch_env()
    patch_logger()
    print("Готово. Патч конфигов/логгера применён.")
