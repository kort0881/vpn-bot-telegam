import re
import io
import random
import aiohttp
import asyncio
from PIL import Image
from datetime import datetime
from bs4 import BeautifulSoup
from aiogram import types, Router, F
from aiogram.filters import Command
from captcha.image import ImageCaptcha
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from texts import get_text
from instruction import instruction_kb
from config import DATA_FILE, ADMIN_ID, CHANNEL_ID, PUBLIC_KEY
from logger import logger, check_subscription, send_logs
from settings import (
    counter_lock,
    get_banned_users,
    get_keys_count,
    write_banned_users,
    write_keys_count,
    get_user_limits,
    write_user_limits,
    add_user_to_banned,
)

# Функция получения данных из API
async def get_key(userid: int) -> dict:
    async with aiohttp.ClientSession() as session:
        jsondata = {
            "public_key": PUBLIC_KEY,
            "user_tg_id": userid,
        }
        headers = {"User-Agent": "chuhan/1.0"}
        try:
            async with session.post(
                "https://vpn-telegram.com/api/v1/key-activate/free-key",
                headers=headers,
                json=jsondata,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    return {"error": f"API error {response.status}"}
                return await response.json()
        except Exception as e:
            return {"error": str(e)}

# НОВАЯ ФУНКЦИЯ: Извлечение vless/vmess ключа со страницы
async def get_raw_key(config_url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(config_url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    # Ищем прямую ссылку на конфиг (vless, vmess, trojan или ss)
                    match = re.search(r'(vless|vmess|ss|trojan)://[^\s"\'<>]+', html)
                    if match:
                        return match.group(0)
        except Exception as e:
            logger.error(f"Ошибка парсинга ключа: {e}")
    return None

# Основной хендлер выдачи ключа
async def parse_key(message: types.Message):
    lang_code = message.from_user.language_code or "en"
    generation_text = get_text(lang_code, "generation")
    user_id = message.from_user.id
    msg = await message.answer(generation_text)

    # --- Проверка лимитов (1 ключ в день) ---
    limits = await get_user_limits(DATA_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    user_info = limits.get(str(user_id), {"date": today, "count": 0, "banned": False})

    if user_info.get("banned"):
        await msg.edit_text(get_text(lang_code, "error", error_msg="вы заблокированы"), parse_mode="HTML")
        return

    if user_info.get("date") == today and user_info.get("count", 0) >= 1:
        await msg.edit_text(get_text(lang_code, "error", error_msg="лимит на сегодня исчерпан"), parse_mode="HTML")
        return

    try:
        user_id_for_api = random.randint(100_000_000, 999_999_999)
        response_data = await get_key(user_id_for_api)

        if "error" in response_data:
            await msg.edit_text(get_text(lang_code, "error", error_msg=response_data["error"]), parse_mode="HTML")
            return

        if response_data.get("result"):
            timestamp = response_data["data"]["finish_at"]
            date = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y, %H:%M")
            vpn_key = response_data["data"]["key"]
            traffic = response_data["data"]["traffic_limit_gb"]
            config_url = f"https://vpn-telegram.com/config/{vpn_key}"

            # --- Извлекаем "сырой" ключ для v2rayNG ---
            raw_key = await get_raw_key(config_url)

            if raw_key:
                result_text = (
                    f"✅ <b>Ваш VPN готов!</b>\n\n"
                    f"<b>Для v2rayNG / Nekobox (нажми, чтобы скопировать):</b>\n"
                    f"de>{raw_key}</code>\n\n"
                    f"<b>Для Hiddify или настройки в браузере:</b>\n"
                    f"{config_url}\n\n"
                    f"📅 Действует до: {date}\n"
                    f"📊 Трафик: {traffic} ГБ"
                )
            else:
                result_text = get_text(lang_code, "key", config_url=config_url, date=date, traffic=traffic)

            await msg.edit_text(result_text, parse_mode="HTML")
            await send_logs(message=message, log="key sent")

            # --- Обновляем глобальный счетчик ---
            async with counter_lock:
                current_keys = await get_keys_count(DATA_FILE)
                await write_keys_count(current_keys + 1, DATA_FILE)

            # --- Обновляем лимит пользователя и автобан ---
            limits = await get_user_limits(DATA_FILE)
            user_info = limits.get(str(user_id), {"date": today, "count": 0, "banned": False})
            
            if user_info.get("date") != today:
                user_info["date"] = today
                user_info["count"] = 0
            
            user_info["count"] += 1
            if user_info["count"] > 3: # Бан, если за день больше 3 успешных ключей
                user_info["banned"] = True
                await add_user_to_banned(user_id, DATA_FILE)
            
            limits[str(user_id)] = user_info
            await write_user_limits(limits, DATA_FILE)

        else:
            error_msg = response_data.get("message", "unknown error")
            await msg.edit_text(get_text(lang_code, "error", error_msg=error_msg), parse_mode="HTML")

    except Exception as e:
        await msg.edit_text(f"Ошибка: {str(e)}")
        await send_logs(message=message, log=f"Error: {str(e)}")

