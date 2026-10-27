# Language texts for the VPN bot.
# Simplified: only the key flow matters — no /check, /api, /donate clutter.

russian = {
    "start": lambda name, keys: f"привет {name}\n\nнажми кнопку ниже, чтобы получить доступ\n\nвсего бот выдал <code>{keys}</code> доступов",
    "generation": "генерация доступа...",
    "error": lambda error_msg: f"ошибка: <code>{error_msg}</code>",
    "any_message": "нажми кнопку ниже, чтобы получить доступ",
    "donate": (
        "💸 <b>Поддержать проект</b>\n\n"
        "Можно угостить нас ☕ кофе, 🍕 пиццей или 🥤 квасом 😉\n\n"
        "⭐ <b>Звёзды Telegram</b> — чаевые в один тап:\n"
        "выберите сумму кнопкой ниже. Если звёзд нет — Telegram предложит купить их прямо при оплате."
    ),
}

english = {
    "start": lambda name, keys: f"hi {name}\n\npress the button below to get a key\n\nin total the bot issued <code>{keys}</code> keys",
    "generation": "generating a key...",
    "error": lambda error_msg: f"error: <code>{error_msg}</code>",
    "any_message": "press the button below to get a key",
    "donate": (
        "💸 <b>Support the project</b>\n\n"
        "⭐ <b>Telegram Stars</b> — one-tap payment:\n"
        "pick an amount below. If you have no stars — Telegram will offer to buy them right in the payment."
    ),
}

uzbek = {
    "start": lambda name, keys: f"salom {name}\n\nkalit olish uchun quyidagi tugmani bosing\n\njami bot <code>{keys}</code> ta kalit berdi",
    "generation": "kalit yaratilmoqda...",
    "error": lambda error_msg: f"xato: <code>{error_msg}</code>",
    "any_message": "kalit olish uchun quyidagi tugmani bosing",
}

ukrainian = {
    "start": lambda name, keys: f"привіт {name}\n\nнатисни кнопку нижче, щоб отримати доступ\n\nвсього бот видав <code>{keys}</code> доступів",
    "generation": "генерація доступа...",
    "error": lambda error_msg: f"помилка: <code>{error_msg}</code>",
    "any_message": "натисни кнопку нижче, щоб отримати доступ",
}

spanish = {
    "start": lambda name, keys: f"hola {name}\n\npulsa el botón de abajo para obtener una clave\n\nen total el bot ha emitido <code>{keys}</code> claves",
    "generation": "generando clave...",
    "error": lambda error_msg: f"error: <code>{error_msg}</code>",
    "any_message": "pulsa el botón de abajo para obtener una clave",
}

arabic = {
    "start": lambda name, keys: f"مرحباً {name}\n\nاضغط على الزر أدناه للحصول على مفتاح\n\nإجمالاً أصدر البوت <code>{keys}</code> مفتاح",
    "generation": "جاري إنشاء المفتاح...",
    "error": lambda error_msg: f"خطأ: <code>{error_msg}</code>",
    "any_message": "اضغط على الزر أدناه للحصول على مفتاح",
}

chinese = {
    "start": lambda name, keys: f"你好 {name}\n\n点击下方按钮获取密钥\n\n机器人已累计发放 <code>{keys}</code> 个密钥",
    "generation": "正在生成密钥...",
    "error": lambda error_msg: f"错误: <code>{error_msg}</code>",
    "any_message": "点击下方按钮获取密钥",
}

persian = {
    "start": lambda name, keys: f"سلام {name}\n\nبرای دریافت کلید روی دکمه زیر کلیک کنید\n\nدر مجموع ربات <code>{keys}</code> کلید صادر کرده است",
    "generation": "در حال تولید کلید...",
    "error": lambda error_msg: f"خطا: <code>{error_msg}</code>",
    "any_message": "برای دریافت کلید روی دکمه زیر کلیک کنید",
}

def get_text(lang_code: str = None, text_key: str = "generation", **kwargs) -> str:
    languages = {
        "ru": russian,
        "en": english,
        "uz": uzbek,
        "uk": ukrainian,
        "es": spanish,
        "ar": arabic,
        "zh": chinese,
        "fa": persian,
        "ir": persian,
    }

    if not lang_code:
        selected_lang = english
    else:
        lang = lang_code.lower()
        selected_lang = english

        for prefix, lang_dict in languages.items():
            if lang.startswith(prefix):
                selected_lang = lang_dict
                break

    if text_key in selected_lang:
        text_func = selected_lang[text_key]

        if callable(text_func):
            return text_func(**kwargs)
        return text_func

    if text_key in english:
        text_func = english[text_key]
        if callable(text_func):
            return text_func(**kwargs)
        return text_func

    return f"[text not found: {text_key}]"
