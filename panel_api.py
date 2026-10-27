"""
3x-UI Panel API client (v3.6.0).
Uses Bearer API token auth (Authorization header).
Adds clients to ALL configured inbounds at once (4-channel setup).
"""
import asyncio
import json
import logging
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from config import (
    PANEL_URL,
    PANEL_API_TOKEN,
    INBOUND_IDS,
    PANEL_RU_URL,
    PANEL_RU_API_TOKEN,
    RU_INBOUND_IDS,
    CASCADE_HOST,
    CASCADE_PORT,
    CASCADE_SNI,
    CASCADE_PATH,
    CASCADE_SECURITY,
    CASCADE_HIDDEN_HOST,
    CASCADE_HIDDEN_PORT,
    CASCADE_HIDDEN_PATH,
    LIMIT_IP,
    TOTAL_BYTES,
    EXPIRE_MS,
    VIP_INBOUND_ID,
    VIP_PORT,
    VIP_PATH,
    VIP_SECURITY,
    VIP_HOST,
    VIP_SNI,
    VIP_SERVER,
    VIP_TOTAL_BYTES,
    SEL_ENABLED,
    SEL_INBOUND_ID,
    SEL_SERVER,
    SEL_PORT,
    SEL_PATH,
    SEL_SNI,
    SEL_TOTAL_BYTES,
)

logger = logging.getLogger(__name__)


@dataclass
class PanelClient:
    email: str
    uuid: str
    links: list  # list of connection links (one per channel)
    total_gb: int
    used_gb: float
    expiry_time: int
    enable: bool
    link: str = ""  # primary link (first channel) for backward compat


class PanelAPIError(Exception):
    pass


class PanelAuthError(PanelAPIError):
    pass


class PanelSession:
    """
    Async session for 3x-ui panel API v3.6.0.
    Auth: Authorization: Bearer ***
    Works against one panel instance (Finland or RU) depending on args.
    """

    def __init__(self, base_url: str = None, api_token: str = None, inbound_ids: list = None):
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = (base_url or PANEL_URL).rstrip("/")
        self._api_token = api_token if api_token is not None else PANEL_API_TOKEN
        self._inbound_ids = inbound_ids if inbound_ids is not None else INBOUND_IDS
        # Limit concurrent requests to the panel (anti-overload).
        self._sem = asyncio.Semaphore(5)
        self._lock = asyncio.Lock()

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=20),
            )

    def _url(self, path: str) -> str:
        """Join base URL and API path (base_url has no trailing slash)."""
        return self._base_url + path

    def _get_auth_headers(self) -> dict:
        """Bearer token auth (3.6.0)."""
        return {"Authorization": f"Bearer {self._api_token}"}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        await self._ensure_session()
        headers = kwargs.pop("headers", {})
        headers.update(self._get_auth_headers())
        if "json" in kwargs or "data" in kwargs:
            headers.setdefault("Content-Type", "application/json")

        # Retry transient failures (panel restart / network blip) with backoff.
        last_err: Exception = None
        for attempt in range(3):
            try:
                async with self._sem:
                    async with self._session.request(
                        method, self._url(path), headers=headers, **kwargs
                    ) as resp:
                        if resp.status == 401 or resp.status == 403:
                            text = await resp.text()
                            raise PanelAuthError(
                                f"Auth failed (HTTP {resp.status}): {text[:200]}"
                            )
                        if resp.status != 200:
                            text = await resp.text()
                            raise PanelAPIError(
                                f"HTTP {resp.status} on {method} {path}: {text[:300]}"
                            )
                        data = await resp.json()
                if not data.get("success"):
                    msg = data.get("msg", "unknown error")
                    raise PanelAPIError(f"API error on {method} {path}: {msg}")
                return data
            except (PanelAuthError,) as e:
                raise  # auth errors are permanent — no retry
            except (PanelAPIError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)  # 1s, 2s
        raise last_err

    async def add_client(self, tg_id: int, username: str) -> PanelClient:
        """Add a client to ALL configured inbounds at once."""
        client_uuid = str(uuid.uuid4())
        email = f"u{tg_id}"

        payload = {
            "client": {
                "email": email,
                "uuid": client_uuid,
                "password": client_uuid,
                "flow": "",
                "limitIp": LIMIT_IP,
                "totalGB": TOTAL_BYTES,
                "expiryTime": int(time.time() * 1000) + EXPIRE_MS,
                "enable": True,
                "tgId": int(tg_id),
            },
            "inboundIds": INBOUND_IDS,
        }

        await self._request("POST", "/panel/api/clients/add", json=payload)
        logger.info(f"Client {email} added to inbounds {self._inbound_ids}")

        # Panel may assign its own uuid — use the real one for links and auth
        real_uuid = await self._get_client_uuid(email) or client_uuid
        if real_uuid != client_uuid:
            logger.info(f"Panel assigned different uuid {real_uuid} for {email}")
            client_uuid = real_uuid

        # Build links from real inbound settings (panel's links API returns a
        # broken spiderX for Reality), fall back to panel links if build fails
        try:
            cfg = await self._get_inbounds_config()
            links = self._build_links(cfg, client_uuid, email)
        except Exception as e:
            logger.warning(f"Link build failed, falling back to panel links: {e}")
            links = []
        if len(links) < len(self._inbound_ids):
            links = await self._get_client_links(email)
            links = self._rewrite_links_host(links)
            links = self._rename_remark(links)
            links = self._fix_client_id(links, client_uuid)

        return PanelClient(
            email=email,
            uuid=client_uuid,
            links=links,
            link=links[0] if links else "",
            total_gb=TOTAL_BYTES,
            used_gb=0.0,
            expiry_time=int(time.time() * 1000) + EXPIRE_MS,
            enable=True,
        )

    async def _get_client_links(self, email: str) -> list:
        """Fetch all connection links generated by the panel."""
        try:
            result = await self._request(
                "GET", f"/panel/api/clients/links/{email}"
            )
            return result.get("obj") or []
        except PanelAPIError as e:
            logger.warning(f"Failed to fetch links for {email}: {e}")
            return []

    @staticmethod
    def _rewrite_links_host(links: list) -> list:
        """Panel generates links with server IP/localhost; use real domain instead."""
        host = "dostupnety.website"
        server_ip = "150.241.86.143"
        out = []
        for link in links:
            if "@localhost" in link:
                link = link.replace("@localhost", f"@{host}", 1)
            if f"@{server_ip}" in link:
                link = link.replace(f"@{server_ip}", f"@{host}", 1)
            # some links may have host=... param
            link = link.replace("host=localhost", f"host={host}")
            link = link.replace(f"host={server_ip}", f"host={host}")
            out.append(link)
        return out

    # Channel port -> human name shown in the client (user's channel brand)
    REMARK_NAMES = {
        443: "Мобильный",
        2083: "Резервный",
        8443: "Домашний",
        8444: "Старый",
    }

    @classmethod
    def _rename_remark(cls, links: list) -> list:
        """Replace the #remark in each link with 'Доступ к интернету — <канал>'."""
        out = []
        for link in links:
            try:
                port = int(re.search(r":(\d+)", link.split("?", 1)[0]).group(1))
            except Exception:
                port = 0
            name = cls.REMARK_NAMES.get(port)
            if name and "#" in link:
                remark = urllib.parse.quote(f"Доступ к интернету — {name}")
                link = link.rsplit("#", 1)[0] + "#" + remark
            out.append(link)
        return out

    @staticmethod
    def _fix_client_id(links: list, client_uuid: str) -> list:
        """
        Panel's links API (3.6.0) sometimes returns a wrong client id in
        vless/trojan links; substitute the real client uuid everywhere.
        """
        out = []
        for link in links:
            m = re.match(r"^(vless|trojan)://([^@]+)@", link)
            if m and m.group(2) != client_uuid:
                link = link.replace(
                    f"{m.group(1)}://{m.group(2)}@",
                    f"{m.group(1)}://{client_uuid}@",
                    1,
                )
            out.append(link)
        return out

    async def _get_client_uuid(self, email: str) -> Optional[str]:
        """Panel 3.6.0 may assign its own client id on add, ignoring ours.
        Fetch the real id from the panel so links/auth match."""
        result = await self._request("GET", "/panel/api/inbounds/list")
        for ib in result.get("obj") or []:
            s = ib.get("settings") or {}
            if isinstance(s, str):
                try:
                    s = json.loads(s)
                except Exception:
                    s = {}
            for cl in s.get("clients") or []:
                if cl.get("email") == email:
                    return cl.get("id")
        return None

    async def _get_inbounds_config(self) -> dict:
        """Fetch streamSettings for the configured inbounds from the panel."""
        result = await self._request("GET", "/panel/api/inbounds/list")
        cfg = {}
        for ib in result.get("obj") or []:
            if ib.get("id") not in self._inbound_ids:
                continue
            ss = ib.get("streamSettings") or {}
            if isinstance(ss, str):
                try:
                    ss = json.loads(ss)
                except Exception:
                    ss = {}
            s = ib.get("settings") or {}
            if isinstance(s, str):
                try:
                    s = json.loads(s)
                except Exception:
                    s = {}
            cfg[ib["id"]] = {
                "protocol": ib.get("protocol"),
                "port": ib.get("port"),
                "ss": ss,
                "clients": [
                    {"email": cl.get("email"), "id": cl.get("id"), "password": cl.get("password")}
                    for cl in (s.get("clients") or [])
                ],
            }
        return cfg

    def _build_links(self, cfg: dict, client_uuid: str, email: str = "") -> list:
        """
        Build connection links manually from inbound settings.
        The panel's own links API returns a broken spiderX (Reality),
        so links are assembled here with the real parameters.
        Note: 3.6.0 may use different credentials per protocol — vless uses
        the panel-assigned id, trojan uses the stored password.
        """
        host = "dostupnety.website"
        links = []
        for ibid in self._inbound_ids:
            info = cfg.get(ibid)
            if not info:
                continue
            ss, port = info["ss"], info["port"]
            net, sec = ss.get("network"), ss.get("security")
            remark = urllib.parse.quote(
                f"Доступ к интернету — {self.REMARK_NAMES.get(port, str(port))}"
            )
            if net == "tcp" and sec == "reality":
                r = ss.get("realitySettings") or {}
                # full masking: sni = the real site in dest (yandex/microsoft),
                # so traffic looks like browsing that site
                dest = r.get("dest") or ""
                sni = dest.split(":")[0] if dest else (r.get("serverNames") or [host])[0]
                link = (
                    f"vless://{client_uuid}@{host}:{port}?encryption=none"
                    f"&flow=xtls-rprx-vision&fp=firefox"
                    f"&pbk={r['settings']['publicKey']}&security=reality"
                    f"&sid={(r.get('shortIds') or [''])[0]}&sni={sni}"
                    f"&spx={urllib.parse.quote((r.get('settings') or {}).get('spiderX') or '/')}"
                    f"&type=tcp"
                )
            elif info["protocol"] == "trojan":
                g = ss.get("grpcSettings") or {}
                # panel stores a separate trojan password (may differ from vless id)
                pw = client_uuid
                for cl in info.get("clients") or []:
                    if cl.get("email") == email and cl.get("password"):
                        pw = cl["password"]
                        break
                link = (
                    f"trojan://{pw}@{host}:{port}?alpn=h2%2Chttp%2F1.1&authority="
                    f"&fp=chrome&security=tls&serviceName={g.get('serviceName', '')}"
                    f"&sni={host}&type=grpc"
                )
            elif net == "ws":
                w = ss.get("wsSettings") or {}
                tls = ss.get("tlsSettings") or {}
                ws_sec = (sec or "none").lower()
                if ws_sec in ("tls", "reality"):
                    addr = host
                    extra = (
                        f"&alpn=h2%2Chttp%2F1.1&fp=firefox"
                        f"&security=tls&sni={tls.get('serverName') or host}"
                    )
                else:
                    # ws без TLS (порт 2087): адрес — IP, никакого sni/fp/alpn,
                    # иначе клиент пытается TLS к серверу, где его нет
                    addr = CASCADE_HOST
                    extra = "&security=none"
                link = (
                    f"vless://{client_uuid}@{addr}:{port}?encryption=none"
                    f"&host={w.get('host') or addr}"
                    f"&path={urllib.parse.quote(w.get('path') or '/')}"
                    f"{extra}&type=ws"
                )
            else:
                continue
            links.append(f"{link}#{remark}")
        return links

    async def update_client(
        self,
        email: str,
        client_uuid: str,
        *,
        enable: bool = True,
        expiry_time: int = None,
        total_bytes: int = None,
        limit_ip: int = None,
    ) -> bool:
        """Обновить клиента (enable/срок/лимит). Реальный контракт 3.8.5."""
        try:
            return await self._post_client_update(
                email, enable=enable, expiry_time=expiry_time,
                total_bytes=total_bytes, limit_ip=limit_ip,
            )
        except PanelAPIError as e:
            if "record not found" in str(e).lower():
                logger.warning(f"Client {email} not found on panel — no update needed")
                return True
            logger.error(f"Error updating client {email}: {e}")
            return False

    async def _clients_list(self) -> list:
        """Все клиенты панели (в записи есть uuid и inboundIds). Кэш 60 с."""
        now = time.time()
        cache = getattr(self, "_clients_cache", None)
        if cache and now - cache[0] < 60:
            return cache[1]
        r = await self._request("GET", "/panel/api/clients/list")
        items = r.get("obj") or []
        self._clients_cache = (now, items)
        return items

    async def _post_client_update(self, email: str, *, enable: bool,
                                  expiry_time: int = None, total_bytes: int = None,
                                  limit_ip: int = None) -> bool:
        """POST /panel/api/clients/update/{email} по контракту 3.8.5.

        Тело — плоский model.Client: "id" = UUID строкой (пустой => панель
        отвечает "empty client ID"), фильтр инбаундов — в query ?inboundIds=.
        Разобрано по исходникам v3.8.5: client_crud.go (UpdateByEmail) и
        client_inbound_apply.go (проверка client.ID).
        """
        rec = next((c for c in await self._clients_list() if c.get("email") == email), None)
        if not rec:
            return True  # клиента нет на этой панели — обновлять нечего
        uuid = (rec.get("uuid") or "").strip()
        if not uuid:
            raise PanelAPIError(f"panel has empty uuid for {email}")
        body = {
            "id": uuid,
            "email": email,
            "subId": rec.get("subId") or "",
            "password": rec.get("password") or "",
            "auth": rec.get("auth") or "",
            "flow": rec.get("flow") or "",
            "security": rec.get("security") or "",
            "limitIp": limit_ip if limit_ip is not None else (rec.get("limitIp") or 2),
            "totalGB": total_bytes if total_bytes is not None else (rec.get("totalGB") or 0),
            "expiryTime": expiry_time if expiry_time is not None else (rec.get("expiryTime") or 0),
            "enable": enable,
            "tgId": rec.get("tgId") or 0,
            "comment": rec.get("comment") or "",
            "reset": 0,
        }
        path = f"/panel/api/clients/update/{email}"
        ib = [int(x) for x in (rec.get("inboundIds") or [])]
        if ib:
            path += "?inboundIds=" + ",".join(str(x) for x in ib)
        await self._request("POST", path, json=body)
        self._clients_cache = None
        logger.info(f"Client {email} updated (uuid={uuid[:8]}, enable={enable}, "
                    f"expiry={body['expiryTime']}, inbounds={ib})")
        return True

    async def refresh_client(self, email: str) -> dict:
        """Переприменить клиента и ПРОВЕРИТЬ перечитыванием (не бутафория)."""
        rec = next((c for c in await self._clients_list() if c.get("email") == email), None)
        if not rec:
            return {"ok": False, "reason": "not_found"}
        before = {k: rec.get(k) for k in ("uuid", "expiryTime", "totalGB", "enable")}
        ok = await self._post_client_update(
            email, enable=True,
            expiry_time=rec.get("expiryTime") or None,
            total_bytes=rec.get("totalGB") or None,
            limit_ip=rec.get("limitIp"),
        )
        if not ok:
            return {"ok": False, "reason": "update_failed"}
        self._clients_cache = None
        check = next((c for c in await self._clients_list() if c.get("email") == email), None)
        if not check:
            return {"ok": False, "reason": "verify_failed"}
        after = {k: check.get(k) for k in ("uuid", "expiryTime", "totalGB", "enable")}
        same = (after["uuid"] == before["uuid"]
                and (not before["expiryTime"] or after["expiryTime"] == before["expiryTime"])
                and after["enable"] is True)
        return {"ok": same, "uuid": after["uuid"], "expiry": after["expiryTime"],
                "total": after["totalGB"], "enable": after["enable"],
                "reason": "ok" if same else "state_changed"}

    async def get_client_traffic(self, email: str) -> Optional[dict]:
        try:
            result = await self._request(
                "GET", f"/panel/api/clients/traffic/{email}"
            )
            return result.get("obj")
        except PanelAPIError:
            logger.warning(f"Failed to get traffic for {email}")
            return None

    async def get_traffic_map(self) -> dict:
        """{email: bytes} — сумма up+down по ВСЕМ инбаундам панели, ОДНИМ запросом.

        Один email живёт в нескольких инбаундах (443/2053/2087/2096), у каждого
        свой счётчик — поэтому суммируем. Нужно циклу синхронизации трафика,
        чтобы не дёргать панель запросом на каждого юзера.
        """
        try:
            r = await self._request("GET", "/panel/api/inbounds/list")
        except PanelAPIError:
            logger.warning("get_traffic_map: failed to list inbounds")
            return {}
        out: dict = {}
        for ib in (r.get("obj") or []):
            for c in (ib.get("clientStats") or []):
                e = c.get("email")
                if not e:
                    continue
                v = (c.get("up") or 0) + (c.get("down") or 0)
                # Панель повторяет один и тот же счётчик клиента в КАЖДОМ инбаунде,
                # где он есть, => суммировать нельзя (даёт xN). Берём максимум —
                # он совпадает с /panel/api/clients/traffic/{email} и с x-ui.
                if e not in out or out[e] < v:
                    out[e] = v
        return out

    async def delete_client(self, email: str):
        """Delete a client by email (removes from all inbounds)."""
        try:
            await self._request(
                "POST", f"/panel/api/clients/del/{email}"
            )
            logger.info(f"Client {email} deleted")
        except PanelAPIError as e:
            logger.error(f"Error deleting client {email}: {e}")

    # ================= VIP / бонусный канал =================
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

    async def add_sel_client(self, tg_id: int) -> Optional[dict]:
        """Создать/переиспользовать клиента канала Selectel 3 ТБ.
        Возвращает {'email','uuid','link'}. Работает на панели Selectel."""
        client_uuid = str(uuid.uuid4())
        email = f"us{tg_id}"
        payload = {
            "client": {
                "email": email,
                "uuid": client_uuid,
                "password": client_uuid,
                "flow": "",
                "limitIp": LIMIT_IP,
                "totalGB": SEL_TOTAL_BYTES,
                "expiryTime": int(time.time() * 1000) + EXPIRE_MS,
                "enable": True,
                "tgId": int(tg_id),
            },
            "inboundIds": [SEL_INBOUND_ID],
        }
        # Выдача через туннель 127.0.0.1:18090 -> DE -> Selectel /issue/<tg>.
        # Бот на финке не видит Selectel напрямую (ТСПУ), туннель решает это.
        try:
            import aiohttp as _aio
            from config import SEL_ISSUE_URL
            import ssl as _ssl
            conn = _aio.TCPConnector(ssl=False)
            async with _aio.ClientSession(connector=conn) as _s:
                async with _s.get(f"{SEL_ISSUE_URL}/issue/{tg_id}",
                                  timeout=_aio.ClientTimeout(total=25)) as resp:
                    data = await resp.json()
            if data.get("ok") and data.get("link"):
                logger.info(f"SEL client {email} issued via tunnel")
                return {"email": data.get("email", email), "uuid": data.get("uuid", client_uuid),
                        "link": data["link"]}
            logger.error(f"SEL issue failed: {data}")
            return None
        except Exception as e:
            logger.error(f"SEL issue exception: {e}")
            return None

    @staticmethod
    def _build_sel_link(client_uuid: str, email: str) -> str:
        """vless-ссылка канала Selectel (WS+TLS через p.rwr-2.top)."""
        q = urllib.parse.urlencode({
            "type": "ws",
            "security": "tls",
            "path": SEL_PATH,
            "host": SEL_SNI,
            "sni": SEL_SNI,
            "fp": "chrome",
            "alpn": "h2,http/1.1",
        })
        remark = urllib.parse.quote("Selectel 3 ТБ")
        return f"vless://{client_uuid}@{SEL_SERVER}:{SEL_PORT}?{q}#{remark}"

    @staticmethod
    def _build_vip_link(client_uuid: str, email: str) -> str:
        """vless-ссылка бонусного входа (WS+TLS через домен)."""
        params = {"type": "ws", "security": VIP_SECURITY, "path": VIP_PATH}
        if (VIP_SECURITY or "").lower() in ("tls", "reality"):
            params.update({"host": VIP_HOST, "sni": VIP_SNI, "fp": "chrome", "alpn": "h2,http/1.1"})
        else:
            params.update({"host": VIP_HOST})
        q = urllib.parse.urlencode(params)
        remark = urllib.parse.quote("Доступ к интернету — Бонус")
        return f"vless://{client_uuid}@{VIP_SERVER}:{VIP_PORT}?{q}#{remark}"

    # ========================================================

    async def get_inbound_info(self) -> Optional[dict]:
        """Get info about the first configured inbound."""
        if not self._inbound_ids:
            return None
        try:
            result = await self._request(
                "GET", f"/panel/api/inbounds/get/{self._inbound_ids[0]}"
            )
            return result.get("obj")
        except PanelAPIError as e:
            logger.error(f"Error getting inbound info: {e}")
            return None

    async def add_cascade_client(self, tg_id: int, username: str) -> Optional[PanelClient]:
        """
        Create a per-user client on the RU panel (cascade entry) and build
        its links: WS-cascade (default) + REALITY :2086 (yandex-маска, для белых списков).
        Returns PanelClient or None.
        """
        client_uuid = str(uuid.uuid4())
        email = f"u{tg_id}"        # 12-09-26: только РЕАЛЬНЫЕ каскадные инбаунды (RU_INBOUND_IDS).
        # REALITY :2086 (id=10) на RU НЕ существует — из выдачи убран.
        inbound_ids = list(dict.fromkeys(list(self._inbound_ids)))
        payload = {
            "client": {
                "email": email,
                "uuid": client_uuid,
                "password": client_uuid,
                "flow": "",
                "limitIp": LIMIT_IP,
                "totalGB": TOTAL_BYTES,
                "expiryTime": int(time.time() * 1000) + EXPIRE_MS,
                "enable": True,
                "tgId": int(tg_id),
            },
            "inboundIds": inbound_ids,
        }
        try:
            await self._request("POST", "/panel/api/clients/add", json=payload)
            logger.info(f"Cascade client {email} added to RU inbounds {inbound_ids}")
        except PanelAPIError as e:
            # Idempotent: if the client already exists (email in use), reuse it
            # instead of failing — important for users created before the
            # cascade channel was added.
            if "already in use" in str(e).lower() or "already" in str(e).lower():
                logger.info(f"Cascade client {email} already exists on RU — reusing")
            else:
                logger.error(f"Failed to add cascade client {email}: {e}")
                return None

        real_uuid = await self._get_client_uuid(email) or client_uuid
        if real_uuid != client_uuid:
            client_uuid = real_uuid

        # Build cascade link (RU server WS+TLS via Cloudflare, CDN-like mask —
        # works in Karing (xhttp is NOT supported by Karing/sing-box, WS is the
        # universal fallback; 10-08-26: 119 legacy users had xhttp links that
        # were useless for Karing — replaced in DB + this generator switched to WS).
        # Address is a DOMAIN (ws-cf.dostupnety.website), path из CASCADE_PATH —
        # канал БЕЗ воркера (RU ib7 :2053 TLS, /ws-cf; 26-08-26).
        remark = urllib.parse.quote("Доступ к интернету — Каскадный")
        # 12-09-26: поддержка обоих режимов входа (TLS-домен / прямой WS по IP).
        _sec = (CASCADE_SECURITY or "tls").strip().lower()
        _path = urllib.parse.quote(CASCADE_PATH, safe="")
        # 12-09-26: ГЛАВНАЯ ссылка — скрытый канал :443 TLS (домен, выглядит как HTTPS).
        remark_hid = urllib.parse.quote("Доступ к интернету — Скрытый (TLS)")
        _hpath = urllib.parse.quote(CASCADE_HIDDEN_PATH, safe="")
        link_hidden = (
            f"vless://{client_uuid}@{CASCADE_HIDDEN_HOST}:{CASCADE_HIDDEN_PORT}?encryption=none"
            f"&fp=chrome&security=tls&sni={CASCADE_HIDDEN_HOST}"
            f"&host={CASCADE_HIDDEN_HOST}&path={_hpath}&type=ws"
            f"#{remark_hid}"
        )
        # Резервная ссылка — прямой WS по IP.
        if _sec == "none":
            link = (
                f"vless://{client_uuid}@{CASCADE_HOST}:{CASCADE_PORT}?encryption=none"
                f"&type=ws&path={_path}&security=none"
                f"#{remark}"
            )
        else:
            link = (
                f"vless://{client_uuid}@{CASCADE_HOST}:{CASCADE_PORT}?encryption=none"
                f"&fp=firefox&security=tls&sni={CASCADE_SNI}"
                f"&host={CASCADE_SNI}&path={_path}&type=ws"
                f"#{remark}"
            )
        links_all = [link_hidden, link]

        return PanelClient(
            email=email,
            uuid=client_uuid,
            links=links_all,
            link=link_hidden,
            total_gb=TOTAL_BYTES,
            used_gb=0.0,
            expiry_time=int(time.time() * 1000) + EXPIRE_MS,
            enable=True,
        )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, *args):
        await self.close()
