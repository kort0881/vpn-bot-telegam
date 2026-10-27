"""Хендлеры TrustTunnel для бота (подключается импортом в main.py)."""
from aiogram import types, F
from aiogram.filters import Command

from user_handlers import router
from tt import issue_tt, TT_HELP, TT_VIDEO_FILE_ID
from logger import logger


async def _send_tt(target, tg_id: int, name: str):
    link, err = await issue_tt(tg_id, name)
    if not link:
        await target.answer(f"⚠️ TrustTunnel сейчас недоступен: {err}")
        return
    if TT_VIDEO_FILE_ID:
        try:
            await target.answer_video(TT_VIDEO_FILE_ID, supports_streaming=True)
        except Exception as e:
            logger.error(f"tt video failed: {e}")
    await target.answer(TT_HELP, parse_mode="HTML", disable_web_page_preview=True)
    # ссылка отдельным сообщением + кнопка копирования в буфер (copy_text, Bot API 7.11+)
    kb = {"inline_keyboard": [[{"text": "📋 Скопировать ссылку",
                                "copy_text": {"text": link}}]]}
    try:
        await target.answer(link, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"tt copy_text button failed: {e}")
        await target.answer(link, disable_web_page_preview=True)


@router.message(Command("tt"))
async def tt_cmd(message: types.Message):
    await _send_tt(message, message.from_user.id, message.from_user.full_name)


@router.callback_query(F.data == "tt_key")
async def tt_key_callback(callback: types.CallbackQuery):
    await callback.answer()
    await _send_tt(callback.message, callback.from_user.id, callback.from_user.full_name)
