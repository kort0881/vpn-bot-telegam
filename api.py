import re
import random
import aiohttp
import base64
from datetime import datetime, timedelta
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
    get_users,
    write_users,
)


# Три GitHub-источника, откуда берутся конфиги
SOURCES = [
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/main/checked/RU_Best/ru_white_part1.txt",
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/main/checked/RU_Best/ru_white_part2.txt",
    "https://raw.githubusercontent.com/kort0881/vpn-checker-backend/main/checked/RU_Best/ru_white_part3.txt",
]


async def get_key(userid: int) -> dict:
    """Получает случайный ключ из GitHub-файлов (один ключ для пользователя)."""
    selected_url = random.choice(SOURCES)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                selected_url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    return {
                        "result": False,
                        "message": f"Ошибка загрузки: {response.status}",
                    }

                text = await response.text()
                lines = [line.strip() for line in text.splitlines() if line.strip()]

                if not lines:
                    return {"result": False, "message": "Нет доступных ключей"}

                vpn_key = random.choice(lines)
                expire_time = int(
                    (datetime.now() + timedelta(days=30)).timestamp()
                )

                return {
                    "result": True,
                    "data": {
                        "key": vpn_key,
                        "config_url": vpn_key,
                        "traffic_limit_gb": 999,
                        "finish_at": expire_time,
                        "status": "1",
                        "is_free": True,
                    },
                }
        except Exception as e:
            logger.error(f"GitHub key fetch error: {e}")
            return {"result": False, "message": str(e)}


async def check_key(config_url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0"}
    result = {"used_gb": None, "expires": None}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                config_url, headers=headers, timeout=10
            ) as response:
                if response.status != 200:
                    return result
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                used_label = soup.find(
                    lambda tag: tag.name == "span"
                    and "Использовано" in tag.text
                )
                if used_label:
                    used_text = used_label.find_next("span").text.strip()
                    match = re.search(r"(\\d+\\.\\d+)", used_text)
                    if match:
                        result["used_gb"] = float(match.group(1))
                expires_label = soup.find(
                    lambda tag: tag.name == "span"
                    and "Действует до" in tag.text
                )
                if expires_label:
                    result["expires"] = (
                        expires_label.find_next("span").text.strip()
                    )
                return result
        except Exception as e:
            logger.error(f"Check key failed: {e}")
            return result


async def get_raw_key(config_url: str) -> str | None:
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                config_url, headers=headers, timeout=10
            ) as response:
                if response.status == 200:
                    html = await response.text()
                    match = re.search(
                        r"(vless|vmess|ss|trojan)://[^\\s\\\"\\'<>]+", html
                    )
                    if match:
                        return match.group(0)
        except Exception:
            pass
    return None


async def build_subscription_50() -> str | None:
    """
    Собиратель подписки: берёт до 50 ссылок из трёх GitHub-файлов
    и возвращает base64-строку для v2rayNG/v2rayN.
    """
    configs: list[str] = []

    async with aiohttp.ClientSession() as session:
        for url in SOURCES:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        continue
                    text = await response.text()
            except Exception as e:
                logger.error(f"Subscription fetch error from {url}: {e}")
                continue

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue

                if not (
                    line.startswith("vless://")
                    or line.startswith("vmess://")
                    or line.startswith("ss://")
                    or line.startswith("trojan://")
                ):
                    continue

                configs.append(line)
                if len(configs) >= 50:
                    break

            if len(configs) >= 50:
                break

    if not configs:
        return None

    raw_text = "\\n".join(configs)
    sub_b64 = base64.b64encode(raw_text.encode("utf-8")).decode("utf-8")
    return sub_b64


async def parse_key(message: types.Message):
    lang_code = message.from_user.language_code or "en"
    generation_text = get_text(lang_code, "generation")
    user_id = message.from_user.id
    msg = await message.answer(generation_text)

    # Сохраняем ID пользователя в базу (DATA_FILE -> "users")
    users = await get_users(DATA_FILE)
    if user_id not in users:
        users.append(user_id)
        await write_users(users, DATA_FILE)

    limits = await get_user_limits(DATA_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    user_info = limits.get(str(user_id), {"date": today, "count": 0, "banned": False})

    if user_info.get("banned"):
        await msg.edit_text(
            get_text(lang_code, "error", error_msg="вы заблокированы"),
            parse_mode="HTML",
        )
        return

    if user_info.get("date") != today:
        user_info["date"] = today
        user_info["count"] = 0

    if user_info.get("count", 0) >= 10:
        await msg.edit_text(
            get_text(lang_code, "error", error_msg="лимит на сегодня исчерпан"),
            parse_mode="HTML",
        )
        return

    try:
        user_id_for_api = random.randint(100_000_000, 999_999_999)
        response_data = await get_key(user_id_for_api)

        if "error" in response_data or not response_data.get("result"):
            await msg.edit_text(
                get_text(
                    lang_code,
                    "error",
                    error_msg=response_data.get("message", "Ошибка API"),
                ),
                parse_mode="HTML",
            )
            return

        if response_data.get("result"):
            timestamp = response_data["data"]["finish_at"]
            date = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y, %H:%M")
            vpn_key = response_data["data"]["key"]
            traffic = response_data["data"]["traffic_limit_gb"]

            result_text = (
                f"✅ <b>Ваш VPN готов!</b>\n\n"
                f"<b>Ключ:</b>\n"
                f"<code>{vpn_key}</code>\n\n"
                f"📅 До: {date}\n"
                f"📊 Трафик: {traffic} ГБ"
            )

            await msg.edit_text(result_text, parse_mode="HTML")
            await send_logs(message=message, log="key sent")

            async with counter_lock:
                current_keys = await get_keys_count(DATA_FILE)
                await write_keys_count(current_keys + 1, DATA_FILE)

            user_info["date"] = today
            user_info["count"] = user_info.get("count", 0) + 1

            if user_info["count"] > 50:
                user_info["banned"] = True
                await add_user_to_banned(user_id, DATA_FILE)

            limits[str(user_id)] = user_info
            await write_user_limits(limits, DATA_FILE)
    except Exception as e:
        logger.error(f"parse_key error: {e}")
        await msg.edit_text(f"Ошибка: {str(e)}")

