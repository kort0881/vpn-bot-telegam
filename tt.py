"""TrustTunnel выдача через панель финки (lucx-ui): вход по логину/паролю панели.

Логин/пароль/адрес панели — только из .env (config.py), в коде их нет.
"""
import json
import secrets

import aiohttp

from config import (
    TT_ENABLED, TT_INBOUND_ID, TT_PANEL_BASE, TT_PANEL_USER, TT_PANEL_PASS,
    TT_VIDEO_FILE_ID,
)

TT_HELP = (
    "🛡 <b>TrustTunnel</b> — отдельное приложение и отдельный ключ.\n"
    "В Karing и HAPP он не работает. Старые ключи остаются как были.\n\n"
    "<b>Шаг 1.</b> Возьми ссылку.\n"
    "Нажми под сообщением с ссылкой кнопку «📋 Скопировать ссылку». Она скопируется целиком, ничего выделять руками не надо.\n\n"
    "<b>Шаг 2.</b> Поставь приложение.\n"
    "Android — Play Store, в поиске «TrustTunnel».\n"
    "Windows — github.com/TrustTunnel/TrustTunnelClient/releases\n\n"
    "<b>Шаг 3.</b> Открой в телефоне любое поле, куда можно вставить текст (например, адресную строку браузера), вставь ссылку и нажми на неё пальцем.\n\n"
    "<b>Шаг 4.</b> Телефон спросит «Открыть приложение TrustTunnel?» — жми «Продолжить».\n\n"
    "<b>Шаг 5.</b> Откроется экран «Add server», поля уже заполнены сами. Жми синюю кнопку «Add» внизу.\n\n"
    "<b>Шаг 6.</b> В списке «Servers» появится строка с адресом 179.198.50.180:8445. Нажми справа от неё круглую кнопку — она станет синей. Значит включено.\n\n"
    "<b>Шаг 7.</b> Проверь: открой 2ip.ru — там должен быть чужой IP, не твой город. Значит работает.\n\n"
    "<b>Если не подключилось:</b> нажми ту же круглую кнопку ещё раз. Второй сервер не добавляй — появится дубль в списке, и будет путаница.\n\n"
    "Видео выше — эти же шаги на телефоне."
)


async def _login(s):
    base = TT_PANEL_BASE
    async with s.get(base + "csrf-token") as r:
        body = await r.text()
        cookie_hdr = r.headers.get("Set-Cookie", "")
    tok = json.loads(body)["obj"]
    cookie = ""
    if "3x-ui=" in cookie_hdr:
        cookie = "3x-ui=" + cookie_hdr.split("3x-ui=", 1)[1].split(";", 1)[0]
    form = {"username": TT_PANEL_USER, "password": TT_PANEL_PASS}
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "X-CSRF-Token": tok, "Cookie": cookie}
    async with s.post(base + "login", data=form, headers=headers) as r:
        body = await r.json(content_type=None)
        new_cookie = r.headers.get("Set-Cookie", "")
    if "3x-ui=" in new_cookie:
        cookie = "3x-ui=" + new_cookie.split("3x-ui=", 1)[1].split(";", 1)[0]
    if not body.get("success"):
        raise RuntimeError(f"panel login failed: {str(body)[:150]}")
    return tok, cookie


async def issue_tt(tg_id: int, name: str = ""):
    if not TT_ENABLED:
        return None, "TrustTunnel пока выключен"
    if not (TT_PANEL_BASE and TT_PANEL_USER and TT_PANEL_PASS):
        return None, "TT-панель не настроена (.env)"
    email = f"ut{tg_id}"
    payload = {
        "client": {
            "email": email,
            "username": email,
            "password": secrets.token_hex(12),
            "name": (name or email)[:40],
            "enable": True,
            "expiryTime": 0,
            "limitIp": 0,
            "totalGB": 0,
            "subId": "",
        },
        "inboundIds": [TT_INBOUND_ID],
    }
    base = TT_PANEL_BASE
    conn = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=40)
    try:
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as s:
            tok, cookie = await _login(s)
            h = {"Cookie": cookie, "X-CSRF-Token": tok, "Content-Type": "application/json"}
            async with s.post(base + "panel/api/clients/add", json=payload, headers=h) as r:
                await r.text()
            async with s.get(base + f"panel/api/clients/links/{email}",
                             headers={"Cookie": cookie, "X-CSRF-Token": tok}) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        return None, f"панель: {e}"

    obj = (data or {}).get("obj") or []
    if isinstance(obj, str):
        obj = [obj]
    link = next((x for x in obj if isinstance(x, str) and x.startswith("tt://")), None)
    if not link:
        return None, f"панель не вернула tt://: {str(data)[:120]}"
    return link, None
