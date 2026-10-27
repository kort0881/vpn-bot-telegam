"""
Inline mode handler.
Shows bot info and redirects to private chat for key generation.
"""
import uuid
from aiogram import Router
from aiogram import types
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from texts import get_text

router = Router()


@router.inline_query()
async def inline_handler(inline_query: InlineQuery):
    """Handle inline queries — redirect to bot for key generation."""
    lang_code = inline_query.from_user.language_code or "ru"
    bot_username = (await inline_query.bot.me()).username

    # Create result: open bot in private chat
    result = InlineQueryResultArticle(
        id=str(uuid.uuid4()),
        title="🔐 Получить доступ",
        description="Нажмите, чтобы открыть бота и получить бесплатный доступ",
        input_message_content=InputTextMessageContent(
            message_text=(
                f"🔐 <b>Бесплатный доступ</b>\n\n"
                f"Нажмите ниже, чтобы открыть бота и получить доступ:\n"
                f"<code>@{bot_username}</code>\n\n"
                f"✅ Подписка на канал — бесплатный доступ"
            ),
            parse_mode="HTML",
        ),
    )

    await inline_query.answer([result], cache_time=10, is_personal=True)
