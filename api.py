import re
import random
import aiohttp
from datetime import datetime
from bs4 import BeautifulSoup
from aiogram import types

from texts import get_text
from config import DATA_FILE, PUBLIC_KEY
from logger import logger, send_logs
from settings import (
    counter_lock,
    get_keys_count,
    write_keys_count,
    get_user_limits,
    write_user_limits,
    add_user_to_banned,
)


async def get_key(userid: int) -> dict:
    async with aiohttp.ClientSession() as session:
        jsondata = {"public_key": PUBLIC_KEY, "user_tg_id": userid}
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


async def check_key(config_url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0"}
    result = {"used_gb": None, "expires": None}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(config_url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    return result
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                used_label = soup.find(
                    lambda tag: tag.name == "span" and "Использовано" in tag.text
                )
                if used_label:
                    used_text = used_label.find_next("span").text.strip()
                    match = re.search(r"(\d+\.\d+)", used_text)
                    if match:
                        result["used_gb"] = float(match.group(1))
                expires_label = soup.find(
                    lambda tag: tag.name == "span" and "Действует до" in tag.text
                )
                if expires_label:
                    result["expires"] = expires_label.find_next("span").text.strip()
                return result
        except Exception as e:
            logger.error(f"Check key failed: {e}")
            return result


async def get_raw_key(config_url: str) -> str | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(config_url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    match = re.search(r'(vless|vmess|ss|trojan)://[^\s"\'<>]+', html)
                    if match:
                        return match.group(0)
        except Exception:
            pass
    return None


async def parse_key(message: types.Message):
    lang_code = message.from_user.language_code or "en"
    generation_text = get_text(lang_code, "generation")
    user_id = message.from_user.id
    msg = await message.answer(generation_text)

    limits = await get_user_limits(DATA_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    user_info = limits.get(str(user_id), {"date": today, "count": 0, "banned": False})

    # Проверка бана
    if user_info.get("banned"):
        await msg.edit_text(
            get_text(lang_code, "error", error_msg="вы заблокированы"),
            parse_mode="HTML",
        )
        return

    # Сброс счётчика при новом дне
    if user_info.get("date") != today:
        user_info["date"] = today
        user_info["count"] = 0

    # Проверка дневного лимита
    if user_info.get("count", 0) >= 1:
        await msg.edit_text(
            get_text(lang_code, "error", error_msg="лимит на сегодня исчерпан"),
            parse_mode="HTML",
        )
        return

    try:
        user_id_for_api = random.randint(100_000_000, 999_999_999)
        response_data = await get_key(user_id_for_api)

        if "error" in response_data:
            await msg.edit_text(
                get_text(lang_code, "error", error_msg=response_data["error"]),
                parse_mode="HTML",
            )
            return

        if response_data.get("result"):
            timestamp = response_data["data"]["finish_at"]
            date = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y, %H:%M")
            vpn_key = response_data["data"]["key"]
            traffic = response_data["data"]["traffic_limit_gb"]
            config_url = f"https://vpn-telegram.com/config/{vpn_key}"

            raw_key = await get_raw_key(config_url)

            if raw_key:
                result_text = (
                    f"✅ <b>Ваш VPN готов!</b>\n\n"
                    f"<b>Для v2rayNG / Nekobox:</b>\n"
                    f"<code>{raw_key}</code>\n\n"  # ← Исправлено!
                    f"<b>Для Hiddify:</b>\n"
                    f"{config_url}\n\n"
                    f"📅 До: {date}\n"
                    f"📊 Трафик: {traffic} ГБ"
                )
            else:
                result_text = (
                    f"✅ <b>Ваш VPN готов!</b>\n\n"
                    f"<b>Ссылка:</b> {config_url}\n"
                    f"📅 До: {date}"
                )

            await msg.edit_text(result_text, parse_mode="HTML")
            await send_logs(message=message, log="key sent")

            async with counter_lock:
                current_keys = await get_keys_count(DATA_FILE)
                await write_keys_count(current_keys + 1, DATA_FILE)

            # Обновление данных пользователя
            user_info["date"] = today
            user_info["count"] = user_info.get("count", 0) + 1

            if user_info["count"] > 3:
                user_info["banned"] = True
                await add_user_to_banned(user_id, DATA_FILE)

            limits[str(user_id)] = user_info
            await write_user_limits(limits, DATA_FILE)
        else:
            await msg.edit_text(
                get_text(lang_code, "error", error_msg=response_data.get("message")),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"parse_key error: {e}")
        await msg.edit_text(f"Ошибка: {str(e)}")
