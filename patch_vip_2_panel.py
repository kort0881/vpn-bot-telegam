#!/usr/bin/env python3
"""Патч 2: VIP-методы в panel_api.py + обработчик кнопки в user_handlers.py.
Идемпотентно. Запускать после patch_vip_1_config.py.
"""
import time

BASE = "/opt/vpn-bot-telegam"


def patch_panel_api():
    p = f"{BASE}/panel_api.py"
    s = open(p, encoding="utf-8").read()
    if "add_vip_client" in s:
        print("panel_api.py: уже пропатчен")
        return

    # 1. импорт VIP-констант
    s = s.replace(
        """    LIMIT_IP,
    TOTAL_BYTES,
    EXPIRE_MS,
)""",
        """    LIMIT_IP,
    TOTAL_BYTES,
    EXPIRE_MS,
    VIP_INBOUND_ID,
    VIP_PORT,
    VIP_PATH,
    VIP_HOST,
    VIP_SNI,
    VIP_SERVER,
    VIP_TOTAL_BYTES,
)""",
        1,
    )

    anchor = "    async def get_inbound_info(self) -> Optional[dict]:"
    add = '''    # ================= VIP / бонусный канал =================
    # Отдельный инбаунд на финке (порт 2096, WS+TLS, path /bonus-ws).
    # Клиент живёт ТОЛЬКО в этом инбаунде и не пересекается с базовым ключом.

    async def add_vip_client(self, tg_id: int) -> Optional[dict]:
        """Создать/переиспользовать VIP-клиента. Возвращает {'email','uuid','link'}."""
        client_uuid = str(uuid.uuid4())
        email = f"uv{tg_id}"
        payload = {
            "client": {
                "email": email,
                "uuid": client_uuid,
                "password": client_uuid,
                "flow": "",
                "limitIp": LIMIT_IP,
                "totalGB": VIP_TOTAL_BYTES,
                "expiryTime": int(time.time() * 1000) + EXPIRE_MS,
                "enable": True,
                "tgId": int(tg_id),
            },
            "inboundIds": [VIP_INBOUND_ID],
        }
        try:
            await self._request("POST", "/panel/api/clients/add", json=payload)
            logger.info(f"VIP client {email} added to inbound {VIP_INBOUND_ID}")
        except PanelAPIError as e:
            if "already" in str(e).lower():
                logger.info(f"VIP client {email} already exists — reusing")
            else:
                logger.error(f"Failed to add VIP client {email}: {e}")
                return None

        real_uuid = await self._get_client_uuid(email) or client_uuid
        link = self._build_vip_link(real_uuid, email)
        return {"email": email, "uuid": real_uuid, "link": link}

    async def add_vip_client_direct(self, tg_id: int) -> Optional[dict]:
        """Обёртка: тот же результат, но без обращения к RU-панели."""
        return await self.add_vip_client(tg_id)

    async def refresh_vip_link(self, tg_id: int) -> Optional[str]:
        """Пересобрать ссылку VIP-клиента (для повторного показа)."""
        email = f"uv{tg_id}"
        real_uuid = await self._get_client_uuid(email)
        if not real_uuid:
            return None
        return self._build_vip_link(real_uuid, email)

    async def set_vip_enabled(self, tg_id: int, enable: bool) -> bool:
        """Включить/выключить VIP-клиента (авто-снятие бонуса при отписке)."""
        email = f"uv{tg_id}"
        real_uuid = await self._get_client_uuid(email)
        if not real_uuid:
            return False  # клиента нет — нечего снимать
        try:
            ok = await self.update_client(email, real_uuid, enable=enable)
            logger.info(f"VIP client {email} -> enable={enable}")
            return bool(ok)
        except Exception as e:
            logger.error(f"VIP enable={enable} failed for {email}: {e}")
            return False

    async def get_vip_traffic(self, tg_id: int) -> Optional[float]:
        """Трафик VIP-клиента в ГБ (для инфо)."""
        try:
            r = await self._request("GET", f"/panel/api/clients/traffic/insider_{tg_id}")
            obj = r.get("obj") or {}
            total = (obj.get("up") or 0) + (obj.get("down") or 0)
            return round(total / (1024 ** 3), 2)
        except PanelAPIError:
            return None

    @staticmethod
    def _build_vip_link(client_uuid: str, email: str) -> str:
        """vless-ссылка бонусного входа (WS+TLS через домен)."""
        q = urllib.parse.urlencode({
            "type": "ws",
            "security": "tls",
            "path": VIP_PATH,
            "host": VIP_HOST,
            "sni": VIP_SNI,
            "fp": "chrome",
            "alpn": "h2,http/1.1",
        })
        remark = urllib.parse.quote("Доступ к интернету — Бонус")
        return f"vless://{client_uuid}@{VIP_SERVER}:{VIP_PORT}?{q}#{remark}"

    # ========================================================

'''
    assert anchor in s, "panel_api.py: якорь не найден"
    s = s.replace(anchor, add + anchor, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("panel_api.py: OK")


if __name__ == "__main__":
    patch_panel_api()
    print("Патч 2 применён.")
