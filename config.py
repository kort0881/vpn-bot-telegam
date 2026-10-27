from os import getenv
from dotenv import load_dotenv

load_dotenv()


# --- Telegram ---
BOT_TOKEN: str = getenv("BOT_TOKEN") or ""
CHANNEL_ID: str = getenv("CHANNEL_ID") or ""
ADMIN_ID: int = int(getenv("ADMIN_ID", "0"))

# --- 3x-UI Panel (Finland — direct channels) ---
PANEL_URL: str = getenv("PANEL_URL", "").rstrip("/")
PANEL_API_TOKEN: str = getenv("PANEL_API_TOKEN", "")  # Bearer token (3.6.0)
INBOUND_IDS: list = [
    int(x) for x in (getenv("INBOUND_IDS", "") or "1,5,4,6").split(",") if x.strip().isdigit()
]

# --- 3x-UI Panel (RU — cascade entry, per-user keys) ---
PANEL_RU_URL: str = getenv("PANEL_RU_URL", "").rstrip("/")
PANEL_RU_API_TOKEN: str = getenv("PANEL_RU_API_TOKEN", "")
RU_INBOUND_IDS: list = [
    int(x) for x in (getenv("RU_INBOUND_IDS", "") or "1").split(",") if x.strip().isdigit()
]
# Cascade entry address (RU server).
# 12-09-26: домен ws-cf/cdn режется по SNI у операторов (RST после ClientHello) —
# вход переведён на ПРЯМОЙ WS по голому IP :2087 (security=none, без SNI/TLS).
# Это единственный вход, через который реально идут все клиенты (19756 соединений).
CASCADE_HOST: str = getenv("CASCADE_HOST", "88.151.114.24")
CASCADE_PORT: int = int(getenv("CASCADE_PORT", "2087"))
CASCADE_SNI: str = getenv("CASCADE_SNI", "")
CASCADE_PATH: str = getenv("CASCADE_PATH", "/ws")
CASCADE_SECURITY: str = getenv("CASCADE_SECURITY", "none")  # none = голый IP WS
# 12-09-26: СКРЫТЫЙ канал :443 TLS (домен ru.dostupnety.website) — основной.
CASCADE_HIDDEN_HOST: str = getenv("CASCADE_HIDDEN_HOST", "ru.dostupnety.website")
CASCADE_HIDDEN_PORT: int = int(getenv("CASCADE_HIDDEN_PORT", "443"))
CASCADE_HIDDEN_PATH: str = getenv("CASCADE_HIDDEN_PATH", "/ws-hidden")

# --- RU REALITY-канал (маска yandex.ru, инбаунд 10 :2086) — новый способ для белых списков ---
# --- REALITY :2086 УДАЛЁН 12-09-26: на RU нет инбаунда id=10 / порта 2086.
#     Бот добавлял клиентов в несуществующий инбаунд и выдавал битую ссылку (1182 юзера).
#     Переменные оставлены пустыми для обратной совместимости импортов.
RU_REALITY_INBOUND: int = 0
RU_REALITY_PORT: int = 0
RU_REALITY_SNI: str = ""
RU_REALITY_PBK: str = ""
RU_REALITY_SID: str = ""
RU_REALITY_SPX: str = ""
RU_REALITY_FLOW: str = ""

# --- Subscription link (sing-box JSON; HAPP via ?f=happ; port 443 через Worker — работает и с белых списков) ---
SUB_URL: str = getenv("SUB_URL", "https://cdn.dostupnety.website/sub/{user_id}")

# --- Fair Use defaults ---
LIMIT_IP: int = int(getenv("LIMIT_IP", "2"))
TOTAL_GB: int = int(getenv("TOTAL_GB", "300"))
EXPIRE_DAYS: int = int(getenv("EXPIRE_DAYS", "30"))

# --- Auto-renewal (subscription check loop) ---
RENEW_INTERVAL_MINUTES: int = int(getenv("RENEW_INTERVAL_MINUTES", "60"))
RENEW_THRESHOLD_DAYS: int = int(getenv("RENEW_THRESHOLD_DAYS", "3"))
# Unsubscribed users can't re-issue a key for this long (anti-flap)
RESUBSCRIBE_BAN_DAYS: int = int(getenv("RESUBSCRIBE_BAN_DAYS", "7"))

# --- VIP / бонусный канал (закрытый канал -> доп. канал на финке) ---
# 16-09-26: подписчикам закрытого канала бонусом выдаётся отдельная ссылка
# на бонусный вход (финка, порт 2096, WS+TLS, path /bonus-ws).
VIP_CHANNEL_ID: str = getenv("VIP_CHANNEL_ID") or ""
VIP_INVITE_URL: str = getenv("VIP_INVITE_URL") or ""
VIP_INBOUND_ID: int = int(getenv("VIP_INBOUND_ID", "8"))
VIP_PORT: int = int(getenv("VIP_PORT", "443"))
VIP_PATH: str = getenv("VIP_PATH", "/ws-hidden")
VIP_SECURITY: str = getenv("VIP_SECURITY", "tls")
VIP_HOST: str = getenv("VIP_HOST", "ru.dostupnety.website")
VIP_SNI: str = getenv("VIP_SNI", "ru.dostupnety.website")
VIP_SERVER: str = getenv("VIP_SERVER", "ru.dostupnety.website")
VIP_TOTAL_BYTES: int = int(getenv("VIP_TOTAL_GB", "300")) * 1024 * 1024 * 1024
# --- Selectel канал (отдельный, 3 ТБ, 01-10-26) ---
SEL_ENABLED: bool = getenv("SEL_ENABLED", "0") == "1"
SEL_PANEL_URL: str = getenv("SEL_PANEL_URL", "")
SEL_API_TOKEN: str = getenv("SEL_API_TOKEN", "")
SEL_INBOUND_ID: int = int(getenv("SEL_INBOUND_ID", "12"))
SEL_SERVER: str = getenv("SEL_SERVER", "p.rwr-2.top")
SEL_PORT: int = int(getenv("SEL_PORT", "443"))
SEL_PATH: str = getenv("SEL_PATH", "/ws-hidden")
SEL_SNI: str = getenv("SEL_SNI", "p.rwr-2.top")
SEL_TOTAL_BYTES: int = int(getenv("SEL_TOTAL_GB", "300")) * 1024 * 1024 * 1024
SEL_ISSUE_URL: str = getenv("SEL_ISSUE_URL", "https://127.0.0.1:18090")


# Convert units
TOTAL_BYTES: int = TOTAL_GB * 1024 * 1024 * 1024
EXPIRE_MS: int = EXPIRE_DAYS * 24 * 60 * 60 * 1000

# --- Paths ---
DB_PATH: str = getenv("DB_PATH", "data/vpn-bot.db")
LOG_DIR: str = getenv("LOG_DIR", "logs")

# --- Validations ---
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required")

if not PANEL_URL:
    raise ValueError("PANEL_URL is required")

if not PANEL_API_TOKEN:
    raise ValueError("PANEL_API_TOKEN is required (Authorization: Bearer)")

if not INBOUND_IDS:
    raise ValueError("INBOUND_IDS must be a comma-separated list of inbound IDs")

# Warn about missing ADMIN_ID but don't fail
if ADMIN_ID <= 0:
    import warnings
    warnings.warn("ADMIN_ID is not set — admin commands and log notifications disabled")


# --- TrustTunnel (Финляндия, инбаунд 'Fin TrustTunnel' :8445) ---
TT_ENABLED: bool = getenv("TT_ENABLED", "1") == "1"
TT_PANEL_URL: str = getenv("TT_PANEL_URL", PANEL_URL)
TT_API_TOKEN: str = getenv("TT_API_TOKEN", PANEL_API_TOKEN)
TT_INBOUND_ID: int = int(getenv("TT_INBOUND_ID", "9"))
TT_PANEL_BASE: str = getenv("TT_PANEL_BASE", "")
TT_PANEL_USER: str = getenv("TT_PANEL_USER", "")
TT_PANEL_PASS: str = getenv("TT_PANEL_PASS", "")
TT_VIDEO_FILE_ID: str = getenv("TT_VIDEO_FILE_ID", "")
