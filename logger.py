"""
Logging configuration with RotatingFileHandler.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

import aiofiles
from aiogram import Bot, types
from aiogram.types import InlineQuery
from aiogram.exceptions import TelegramBadRequest

from config import CHANNEL_ID, ADMIN_ID, LOG_DIR, VIP_CHANNEL_ID

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Configure root logger with rotation (10 MB per file, 5 backups)
_rotation_handler = RotatingFileHandler(
    filename=os.path.join(LOG_DIR, "vpn-bot.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
_rotation_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_rotation_handler, _stream_handler],
)

logger = logging.getLogger(__name__)


# ---- Subscription check ----

async def check_subscription(bot: Bot, user_id: int, channel_id: str) -> bool:
    """
    Check if user is subscribed to the required channel.
    Returns True if subscribed or check is impossible (graceful degradation).
    """
    if not channel_id:
        logger.warning("CHANNEL_ID not configured, skipping subscription check")
        return True

    try:
        chat_member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        valid_statuses = {"member", "administrator", "creator"}
        if chat_member.status in valid_statuses:
            return True
        elif chat_member.status == "restricted":
            return chat_member.is_member
        else:
            return False

    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower() or "bot is not a member" in str(e).lower():
            logger.warning(f"Bot cannot check subscription to {channel_id}: {e}")
            return True  # Graceful degradation
        logger.error(f"Telegram error checking subscription: {e}")
        return True
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        return True


async def check_vip_subscription(bot: Bot, user_id: int) -> bool:
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


# ---- Admin log notifications ----

async def send_logs(
    message: types.Message = None,
    inline_query: InlineQuery = None,
    log: str = None,
    user_id: int = None,
):
    """Send notification log to admin via Telegram."""
    if message:
        # IMPORTANT: for callbacks message.from_user is the BOT —
        # pass user_id explicitly from callback.from_user.id.
        if user_id is None:
            user_id = message.from_user.id
        username = message.from_user.username or "not_found"
        context = "message"
    elif inline_query:
        user_id = inline_query.from_user.id
        username = inline_query.from_user.username or "not_found"
        context = "inline"
    else:
        logger.error("send_logs called without message or inline_query")
        return

    log_message = f"[{context}] {log}, user_id={user_id}, username=@{username}"
    logger.info(log_message)

    if ADMIN_ID:
        bot = message.bot if message else (inline_query.bot if inline_query else None)
        if bot:
            try:
                await bot.send_message(chat_id=ADMIN_ID, text=log_message)
            except Exception as e:
                logger.error(f"Failed to send log to admin: {e}")
