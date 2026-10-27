"""Единый текст для забаненных: «так нечестно» + сколько ждать. 16-10-26.

Правило владельца: забаненный за попытку получить доступ без поддержки должен
видеть человеческое объяснение и срок, а не сухое «Вы забанены».
Срок считаем от unsubscribed_at + RESUBSCRIBE_BAN_DAYS (отдельной даты бана в БД нет).
"""
import time

try:
    from config import RESUBSCRIBE_BAN_DAYS
except Exception:
    RESUBSCRIBE_BAN_DAYS = 7

_BASE = (
    "🤔 Так нечестно.\n\n"
    "Доступ выдаётся тем, кто реально поддерживает проект, а не просто "
    "нажимает кнопку. Мы это видим.\n\n"
)


_TERM = "Подожди {d} и заходи честно — доступ вернём."


def _term(unsubscribed_at) -> str:
    """Срок ожидания: от даты отписки, иначе без точного срока."""
    try:
        if unsubscribed_at:
            left = (int(unsubscribed_at) + RESUBSCRIBE_BAN_DAYS * 86400 * 1000
                    - time.time() * 1000)
            days = int(left / (86400 * 1000)) + 1
            if days > 0:
                return _TERM.format(d=("1 день" if days == 1 else f"{days} дн."))
    except Exception:
        pass
    return _TERM.format(d="недельку")


def banned_text(unsubscribed_at=None, ban_reason=None) -> str:
    """Текст для забаненного: причина (если записана) + срок ожидания."""
    reason = (ban_reason or "").strip()
    if reason:
        return _BASE + f"Причина: {reason}\n\n" + _term(unsubscribed_at)
    return _BASE + _term(unsubscribed_at)
