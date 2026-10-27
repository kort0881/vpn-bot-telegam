"""Не-анонимный опрос-кнопки в канале: нажатие inline-кнопки несёт telegram_id пользователя.
Пишем (user, вариант) в poll_answers (poll_id='channel_survey_2026-10-09')."""
from aiogram import Router, types, F
from logger import logger

router = Router()
POLL_ID = "channel_survey_2026-10-09"
LABELS = {1: "Всё работает стабильно", 2: "Работает, но иногда отваливается",
          3: "Медленно, но работает", 4: "Не работает / не подключается"}


@router.callback_query(F.data.startswith("sv:"))
async def on_survey_vote(callback: types.CallbackQuery):
    u = callback.from_user
    try:
        opt = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer()
        return
    db = getattr(callback.bot, "db", None)
    if db is not None:
        try:
            await db.record_poll_answer(POLL_ID, u.id, u.username, u.full_name, [opt - 1])
        except Exception as e:
            logger.error(f"survey record error: {e}")
    try:
        await callback.answer("Спасибо! Записали: " + LABELS.get(opt, "?"))
    except Exception:
        pass
    logger.info(f"SURVEY_VOTE user={u.id} opt={opt}")
