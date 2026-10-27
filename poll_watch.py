"""Сбор ответов НЕ-анонимного опроса: PollAnswer -> таблица poll_answers.
Нужен, чтобы связать голос с telegram_id (атрибуция «что и у кого не работает»)."""
from aiogram import Router, Bot
from aiogram.types import PollAnswer
from logger import logger

router = Router()


@router.poll_answer()
async def on_poll_answer(poll_answer: PollAnswer, bot: Bot):
    try:
        u = poll_answer.user
        opts = list(poll_answer.option_ids or [])
        db = getattr(bot, "db", None)
        if db is not None:
            await db.record_poll_answer(poll_answer.poll_id, u.id, u.username, u.full_name, opts)
        logger.info(f"POLL_ANSWER poll={poll_answer.poll_id} user={u.id} opts={opts}")
    except Exception as e:
        logger.error(f"poll_answer handler error: {e}")
