"""Умный ответ в поддержку: бот сам отвечает через внешний ИИ (Gemini/Groq).

Работает ТОЛЬКО если в /opt/vpn-bot-telegam/.env есть ключ (GEMINI_API_KEY / GROQ_API_KEY).
Нет ключа или ошибка/лимит — возвращаем None, и бот отдаёт обычный автоответчик
(поведение не меняется, поддержка не ломается).

Прокси: если задан SMART_PROXY (например socks5h://127.0.0.1:1080), запрос идёт через него.
"""
import json
import logging
import os
import re
import subprocess
import time
import tempfile

logger = logging.getLogger(__name__)

ENV_PATH = os.getenv("BOT_ENV_PATH", "/opt/vpn-bot-telegam/.env")
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

# слова, которые не должны появляться в ответе ни при каких условиях
FORBIDDEN = ("vpn", "впн", "vless", "reality", "trojan", "ключ", "оплат", "тариф",
             "обход", "блокиров", "тспу", "ркн", "dns", "anti-detect", "отпечат",
             "tls", "sni", "каскад", "цензур")

SYSTEM = (
    "Ты — поддержка сервиса «Доступ к интернету» в Telegram. Пишешь от лица сервиса, "
    "по-человечески, коротко и спокойно, на русском.\n"
    "Что нужно знать о сервисе: доступ бесплатный, выдаётся ботом одной персональной ссылкой; "
    "ссылку человек вставляет в своё приложение; внутри ссылки несколько маршрутов, "
    "их можно переключать.\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Не используй слова: VPN, VLESS, REALITY, Trojan, ключ, оплата, тариф, обход, "
    "блокировки, ТСПУ, РКН, DNS, отпечаток, TLS, SNI, каскад.\n"
    "2. Никаких технических инструкций и объяснений, как что-то обходить.\n"
    "3. Максимум, что можно посоветовать: обновить ссылку, выбрать другой маршрут, "
    "проверить на Wi-Fi и мобильной сети, перезапустить приложение, написать сюда ещё раз.\n"
    "4. Подробные настройки не расписывай — скажи, что инструкция в закрытом канале.\n"
    "5. Вопросы про деньги, возврат, конфликты — не решай: скажи, что передал человеку, "
    "он ответит лично.\n"
    "6. Не выдумывай факты про сроки и трафик — используй только то, что дано ниже.\n"
    "7. Ответ — только сам текст ответа клиенту, не больше трёх предложений."
    " Это не задание написать текст, а живое обращение. Не пересказывай эти правила, "
    "не перечисляй данные о клиенте, не пиши служебных слов вроде «факты», «ответ», «3 предложения»."
)


def _env(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v:
        return v.strip()
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


def _curl_json(url: str, payload: dict, headers=None, timeout: int = 30):
    """POST через curl: работает и с прокси, и без; python-urllib тут ни при чём."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        path = f.name
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", "POST", url,
           "-H", "Content-Type: application/json", "--data-binary", f"@{path}"]
    for h in headers or []:
        cmd += ["-H", h]
    proxy = _env("SMART_PROXY")
    if proxy:
        cmd += ["--proxy", proxy]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if p.returncode != 0:
        raise RuntimeError(f"curl rc={p.returncode}: {p.stderr.strip()[:200]}")
    return json.loads(p.stdout)

def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text


# слово ловим по началу токена: «блокировок» поймается, а «переключи» — нет
_BOUND = re.compile(
    r"(?<![а-яёa-z])(" + "|".join(re.escape(w) for w in FORBIDDEN) + r")[а-яёa-z]*")


def _stop_words(text: str) -> list:
    """Запрещённые слова как ЦЕЛЫЕ слова: «переключи» — не «ключ»."""
    return sorted({m.group(1) for m in _BOUND.finditer(text.lower())})


def _looks_safe(text: str) -> bool:
    return not _stop_words(text)


def _gemini_models() -> list:
    """Порядок моделей: сначала наша, потом запасные — у бесплатного тарифа бывают всплески 503."""
    first = _env("SMART_MODEL", DEFAULT_GEMINI_MODEL)
    chain = [first, "gemini-flash-latest", "gemini-3-flash-preview", "gemini-3.1-flash", "gemini-2.5-pro"]
    out = []
    for m in chain:
        if m and m not in out:
            out.append(m)
    return out


def _ask_gemini(question: str, facts: str) -> str | None:
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not key:
        return None
    for model in _gemini_models():
        for attempt in (1, 2):
            try:
                text = _gemini_call(model, key, question, facts)
            except Exception as e:
                logger.warning(f"smart_reply: {model} попытка {attempt}: {e}")
                text = None
            if text:
                return text
            time.sleep(1.5)
    return None


def _gemini_call(model: str, key: str, question: str, facts: str) -> str | None:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": f"Данные о клиенте (пересказывать не нужно): {facts}\n\nВопрос клиента: {question}"}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 400,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    data = _curl_json(url, payload)
    if "error" in data:
        raise RuntimeError(f"{model}: {data['error'].get('message','')[:120]}")
    cand = (data.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts") or [])
    text = " ".join(p.get("text", "") for p in parts if p.get("text") and not p.get("thought"))
    if not text.strip():
        raise RuntimeError(f"{model}: пустой ответ, finishReason={cand.get('finishReason')}")
    return _clean(text) or None


def _ask_groq(question: str, facts: str) -> str | None:
    key = _env("GROQ_API_KEY")
    if not key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": _env("GROQ_MODEL", "openai/gpt-oss-120b"),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": f"{facts}\n\nОбращение: {question}"}],
        "temperature": 0.4, "max_tokens": 300,
    }
    data = _curl_json(url, payload, headers=[f"Authorization: Bearer {key}"])
    try:
        return _clean(data["choices"][0]["message"]["content"]) or None
    except Exception:
        return None


def _providers() -> list:
    """Порядок провайдеров: сначала быстрый (Groq), потом Gemini. Есть ключ — пробуем."""
    out = []
    if _env("GROQ_API_KEY"):
        out.append(_ask_groq)
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        out.append(_ask_gemini)
    return out


def answer(question: str, facts: str = "") -> str | None:
    """Вернёт текст ответа или None (тогда бот отвечает как раньше)."""
    if _env("SMART_REPLY", "on").lower() in ("off", "0", "no", "выкл"):
        return None                      # рубильник: одной строкой в .env
    if not question or len(question) < 2:
        return None
    for provider in _providers():
        for attempt in (1, 2):
            try:
                text = provider(question, facts)
            except Exception as e:
                logger.warning(f"smart_reply: {provider.__name__}: {e}")
                text = None
            if not text:
                continue
            if len(text) > 900:
                text = text[:900].rsplit(" ", 1)[0] + "…"
            if _looks_safe(text):
                return text
            # в ответе проскочили запрещённые слова — просим переписать один раз
            logger.warning(f"smart_reply: в ответе запрещённые слова {_stop_words(text)}, прошу переписать")
            question = (question + "\n\nВАЖНО: перепиши ответ, не используя слова: "
                        + ", ".join(FORBIDDEN[:12]))
    return None


def build_facts(user: dict | None, node_ok: bool | None = None) -> str:
    """Короткая фактическая справка о человеке — чтобы ИИ не выдумывал."""
    if not user:
        return "Факты: доступа у человека ещё нет (он не получал ссылку)."
    import datetime
    parts = []
    parts.append("Факты:")
    parts.append(f"доступ выдан: {'да' if user.get('is_active') else 'нет'}")
    exp = user.get("expiry_at")
    if exp:
        try:
            parts.append("действует до " + datetime.datetime.fromtimestamp(exp / 1000).strftime("%d.%m.%Y"))
        except Exception:
            pass
    used = (user.get("used_bytes") or 0) / (1024 ** 3)
    total = (user.get("total_bytes") or 0) / (1024 ** 3)
    if total:
        parts.append(f"использовано {used:.1f} ГБ из {total:.0f} ГБ")
    if user.get("banned"):
        parts.append("доступ заблокирован отпиской")
    if node_ok is not None:
        parts.append("сервис сейчас работает" if node_ok else "на стороне сервиса сейчас техническая пауза")
    return ", ".join(parts) + "."
