"""
Async SQLite database for VPN bot user management.
"""
import logging
import json
import sqlite3
import time
from datetime import datetime
from typing import Optional

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)

# SQL for table creation
CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT DEFAULT '',
    email           TEXT UNIQUE NOT NULL,       -- "tg_{telegram_id}" in panel
    client_uuid     TEXT NOT NULL,               -- client UUID in 3x-ui
    vless_link      TEXT NOT NULL,               -- primary connection link
    links           TEXT DEFAULT '[]',           -- JSON array of all 4 channel links
    total_bytes     INTEGER NOT NULL DEFAULT 0,
    used_bytes      REAL NOT NULL DEFAULT 0.0,
    created_at      INTEGER NOT NULL,            -- unix timestamp
    expiry_at       INTEGER NOT NULL,            -- unix timestamp (ms)
    is_active       INTEGER NOT NULL DEFAULT 1,  -- 1=active, 0=expired/banned
    banned          INTEGER NOT NULL DEFAULT 0,
    unsubscribed_at INTEGER NOT NULL DEFAULT 0,  -- ms epoch when disabled for unsubscribing (resubscribe ban)
    channel_step    INTEGER NOT NULL DEFAULT 0,  -- step in step-by-step key issuance
    chosen_channel  INTEGER DEFAULT -1,          -- channel user confirmed works (-1=not chosen)
    ban_reason      TEXT DEFAULT '',             -- why banned (16-10-26)
    ban_at          INTEGER NOT NULL DEFAULT 0   -- when banned, ms epoch
)
"""

CREATE_KEYS_COUNT_TABLE = """
CREATE TABLE IF NOT EXISTS stats (
    key     TEXT PRIMARY KEY,
    value   INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_SUPPORT_TABLE = """
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    username TEXT,
    full_name TEXT,
    text TEXT,
    created_at INTEGER,
    status TEXT DEFAULT 'new'
)
"""

CREATE_SUPPORT_INDEX = "CREATE INDEX IF NOT EXISTS idx_support_created ON support_messages(created_at)"



CREATE_PAYMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id   INTEGER NOT NULL,
    username      TEXT    DEFAULT '',
    full_name     TEXT    DEFAULT '',
    amount        INTEGER NOT NULL DEFAULT 0,
    method        TEXT    DEFAULT 'sbp',
    status        TEXT    DEFAULT 'claimed',
    created_at    INTEGER NOT NULL,
    confirmed_at  INTEGER NOT NULL DEFAULT 0
)
"""


class Database:
    """Manages the SQLite database for bot users."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open connection and create tables if not exist."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute(CREATE_USERS_TABLE)
        await self._conn.execute(CREATE_KEYS_COUNT_TABLE)
        await self._conn.execute(CREATE_SUPPORT_TABLE)
        await self._conn.execute(CREATE_SUPPORT_INDEX)
        await self._conn.execute(CREATE_PAYMENTS_TABLE)
        await self._conn.execute(CREATE_POLL_ANSWERS_TABLE)
        # Migration: add `links` column if missing (older DBs)
        cols = await self._conn.execute("PRAGMA table_info(users)")
        col_names = [row["name"] for row in await cols.fetchall()]
        if "links" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN links TEXT DEFAULT '[]'")
        if "channel_step" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN channel_step INTEGER NOT NULL DEFAULT 0")
        if "chosen_channel" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN chosen_channel INTEGER DEFAULT -1")
        if "unsubscribed_at" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN unsubscribed_at INTEGER NOT NULL DEFAULT 0")
        if "last_seen" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
        if "visits" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN visits INTEGER NOT NULL DEFAULT 0")
        if "ban_reason" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''")
        if "ban_at" not in col_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN ban_at INTEGER NOT NULL DEFAULT 0")
        await self._conn.commit()
        logger.info(f"Database connected: {self.db_path}")

    # ===================== PAYMENTS =====================

    async def add_payment(self, telegram_id: int, username: str, full_name: str,
                          amount: int, method: str = "sbp") -> int:
        """Заявка «я оплатил» -> id записи (status=claimed)."""
        cur = await self._conn.execute(
            "INSERT INTO payments (telegram_id, username, full_name, amount, method, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'claimed', ?)",
            (int(telegram_id), username or "", full_name or "", int(amount), method,
             int(time.time() * 1000)),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def confirm_payment(self, payment_id: int) -> bool:
        cur = await self._conn.execute(
            "UPDATE payments SET status='confirmed', confirmed_at=? "
            "WHERE id=? AND status='claimed'",
            (int(time.time() * 1000), int(payment_id)),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_payment(self, payment_id: int) -> Optional[dict]:
        cur = await self._conn.execute("SELECT * FROM payments WHERE id=?", (int(payment_id),))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_payments(self, limit: int = 30) -> list:
        cur = await self._conn.execute(
            "SELECT * FROM payments ORDER BY id DESC LIMIT ?", (int(limit),)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def payments_stats(self) -> dict:
        cur = await self._conn.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN status='confirmed' THEN amount ELSE 0 END),0) AS total_confirmed, "
            "COUNT(CASE WHEN status='confirmed' THEN 1 END) AS cnt_confirmed, "
            "COUNT(CASE WHEN status='claimed' THEN 1 END) AS cnt_claimed "
            "FROM payments"
        )
        row = await cur.fetchone()
        return dict(row) if row else {"total_confirmed": 0, "cnt_confirmed": 0, "cnt_claimed": 0}

    async def get_user_payments(self, telegram_id: int) -> list:
        cur = await self._conn.execute(
            "SELECT * FROM payments WHERE telegram_id=? AND status='confirmed' ORDER BY id DESC",
            (int(telegram_id),)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


    async def record_poll_answer(self, poll_id, telegram_id, username, full_name, option_ids):
        """Сохранить ответ не-анонимного опроса (атрибуция к telegram_id)."""
        await self._conn.execute(CREATE_POLL_ANSWERS_TABLE)
        import time as _t
        await self._conn.execute(
            "INSERT INTO poll_answers(poll_id,telegram_id,username,full_name,option_ids,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (str(poll_id), int(telegram_id), username or "", full_name or "",
             ",".join(str(x) for x in (option_ids or [])), int(_t.time()*1000)),
        )
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    # ---- User operations ----

    async def get_user(self, telegram_id: int) -> Optional[dict]:
        """Get user by telegram_id. Returns dict or None."""
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def touch_user(self, telegram_id: int) -> None:
        """Отметить визит: last_seen=now, visits+=1. Кросс-проверка возвратов."""
        try:
            await self._conn.execute(
                "UPDATE users SET last_seen = ?, visits = visits + 1 WHERE telegram_id = ?",
                (int(datetime.now().timestamp()), int(telegram_id)),
            )
            await self._conn.commit()
        except Exception as e:
            logger.warning(f"touch_user({telegram_id}) failed: {e}")

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        """Get user by email (tg_{id})."""
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def add_user(
        self,
        telegram_id: int,
        username: str,
        email: str,
        client_uuid: str,
        vless_link: str,
        total_bytes: int,
        expiry_at: int,
        links: list = None,
    ) -> bool:
        """Insert a new user. Returns True on success, False if duplicate."""
        try:
            now = int(datetime.now().timestamp())
            links_json = json.dumps(links or [])
            await self._conn.execute(
                """INSERT INTO users 
                   (telegram_id, username, email, client_uuid, vless_link, links,
                    total_bytes, created_at, expiry_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (telegram_id, username, email, client_uuid, vless_link, links_json,
                 total_bytes, now, expiry_at),
            )
            await self._conn.commit()
            # Increment keys counter
            await self._increment_keys_count()
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"User {telegram_id} already exists")
            return False

    async def users_due_for_report(self, period_ms: int = 30 * 24 * 3600 * 1000) -> list:
        """Активные+не забаненные юзеры, которым пора месячный отчёт."""
        cur = await self._conn.execute(
            "SELECT * FROM users WHERE is_active = 1 AND banned = 0 "
            "AND (last_report_at = 0 OR ? - last_report_at >= ?)",
            (int(time.time() * 1000), period_ms),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_report_sent(self, telegram_id: int, ts_ms: int):
        await self._conn.execute(
            "UPDATE users SET last_report_at = ? WHERE telegram_id = ?",
            (ts_ms, telegram_id),
        )
        await self._conn.commit()

    async def update_user_traffic(self, telegram_id: int, used_bytes: float):
        """Update used traffic for a user."""
        await self._conn.execute(
            "UPDATE users SET used_bytes = ? WHERE telegram_id = ?",
            (used_bytes, telegram_id),
        )
        await self._conn.commit()

    async def bulk_update_traffic(self, by_email: dict) -> int:
        """Массово обновить used_bytes по {email: bytes}. Возвращает число строк."""
        if not by_email:
            return 0
        rows = [(int(v), e) for e, v in by_email.items()]
        cur = await self._conn.executemany(
            "UPDATE users SET used_bytes = ? WHERE email = ?", rows
        )
        await self._conn.commit()
        n = cur.rowcount
        return n if n and n > 0 else 0

    async def update_user_expiry(self, telegram_id: int, expiry_at: int):
        """Set new expiry timestamp (ms) for a user."""
        await self._conn.execute(
            "UPDATE users SET expiry_at = ? WHERE telegram_id = ?",
            (expiry_at, telegram_id),
        )
        await self._conn.commit()

    async def set_user_active(self, telegram_id: int, is_active: bool):
        """Set user active flag (1=active, 0=disabled/expired)."""
        await self._conn.execute(
            "UPDATE users SET is_active = ? WHERE telegram_id = ?",
            (1 if is_active else 0, telegram_id),
        )
        await self._conn.commit()

    async def set_unsubscribed_at(self, telegram_id: int, ts_ms: int):
        """Record when the user was disabled for unsubscribing (resubscribe ban)."""
        await self._conn.execute(
            "UPDATE users SET unsubscribed_at = ? WHERE telegram_id = ?",
            (ts_ms, telegram_id),
        )
        await self._conn.commit()

    async def clear_unsubscribed_at(self, telegram_id: int):
        """Reset the resubscribe-ban timestamp (user re-subscribed legitimately)."""
        await self._conn.execute(
            "UPDATE users SET unsubscribed_at = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await self._conn.commit()

    async def set_channel_step(self, telegram_id: int, step: int):
        """Set current issuance step (index into the channel list)."""
        await self._conn.execute(
            "UPDATE users SET channel_step = ? WHERE telegram_id = ?",
            (step, telegram_id),
        )
        await self._conn.commit()

    async def update_user_links(self, telegram_id: int, links: list):
        """Replace stored links (JSON array) for a user."""
        await self._conn.execute(
            "UPDATE users SET links = ? WHERE telegram_id = ?",
            (json.dumps(links), telegram_id),
        )
        await self._conn.commit()

    async def set_chosen_channel(self, telegram_id: int, idx: int):
        """Remember which channel the user confirmed works."""
        await self._conn.execute(
            "UPDATE users SET chosen_channel = ? WHERE telegram_id = ?",
            (idx, telegram_id),
        )
        await self._conn.commit()

    async def deactivate_user(self, telegram_id: int):
        """Mark user as inactive (expired)."""
        await self._conn.execute(
            "UPDATE users SET is_active = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await self._conn.commit()

    async def ban_user(self, telegram_id: int, reason: str = ""):
        """Ban a user. reason — short reason (logged + shown to user)."""
        import time as _t
        await self._conn.execute(
            "UPDATE users SET banned = 1, is_active = 0, ban_reason = ?, ban_at = ? "
            "WHERE telegram_id = ?",
            (reason or "", int(_t.time() * 1000), telegram_id),
        )
        await self._conn.commit()

    async def unban_user(self, telegram_id: int):
        """Unban a user and clear stored ban reason."""
        await self._conn.execute(
            "UPDATE users SET banned = 0, ban_reason = '', ban_at = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await self._conn.commit()

    async def get_banned_users(self) -> list[int]:
        """Get list of banned telegram IDs."""
        cursor = await self._conn.execute(
            "SELECT telegram_id FROM users WHERE banned = 1"
        )
        rows = await cursor.fetchall()
        return [row["telegram_id"] for row in rows]

    async def get_all_users(self) -> list[dict]:
        """Get all users."""
        cursor = await self._conn.execute("SELECT * FROM users ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_active_users(self) -> list[dict]:
        """Get active (non-expired, non-banned) users."""
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE is_active = 1 AND banned = 0 ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_count(self) -> int:
        """Get total number of users."""
        cursor = await self._conn.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ---- Stats operations ----

    async def _increment_keys_count(self):
        """Increment the total keys counter."""
        await self._conn.execute(
            """INSERT INTO stats (key, value) VALUES ('total_keys', 1)
               ON CONFLICT(key) DO UPDATE SET value = value + 1""",
        )
        await self._conn.commit()

    async def get_keys_count(self) -> int:
        """Get total number of keys ever issued."""
        cursor = await self._conn.execute(
            "SELECT value FROM stats WHERE key = 'total_keys'"
        )
        row = await cursor.fetchone()
        return row["value"] if row else 0

    # ---- Ban list compatibility (for existing code that uses get_banned_users) ----

    async def get_banned_list(self) -> list[int]:
        return await self.get_banned_users()

    async def save_support_message(self, telegram_id: int, username: str,
                                   full_name: str, text: str) -> None:
        """Сохраняет обращение юзера в поддержку."""
        try:
            now_ms = int(datetime.now().timestamp() * 1000)
            await self._conn.execute(
                """INSERT INTO support_messages (telegram_id, username, full_name, text, created_at) VALUES (?, ?, ?, ?, ?)""",
                (telegram_id, username or "", full_name or "", text, now_ms),
            )
            await self._conn.commit()
        except Exception as e:
            logger.warning(f"save_support_message failed: {e}")

    async def mark_support_auto_answered(self, telegram_id: int, text: str) -> None:
        """Помечает последнее обращение юзера как auto_answered.

        22-09-26: бот отвечает на типовые вопросы сам (автоответчик, 0 токенов).
        Чтобы владелец не разбирал то, что уже объяснено, такие записи получают
        статус 'auto_answered' (в /support видно отдельной меткой). Ищем строго
        последнюю запись этого юзера с тем же текстом и статусом 'new'.
        """
        try:
            await self._conn.execute(
                """UPDATE support_messages SET status = 'auto_answered'
                   WHERE id = (SELECT id FROM support_messages
                               WHERE telegram_id = ? AND text = ? AND status = 'new'
                               ORDER BY id DESC LIMIT 1)""",
                (telegram_id, text),
            )
            await self._conn.commit()
        except Exception as e:
            logger.warning(f"mark_support_auto_answered failed: {e}")

    async def resolve_support_message(self, msg_id: int) -> bool:
        """Помечает обращение как обработанное."""
        try:
            await self._conn.execute(
                """UPDATE support_messages SET status = 'resolved' WHERE id = ?""",
                (msg_id,),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"""resolve_support_message failed: {e}""")
            return False

    async def count_new_support(self) -> int:
        """Сколько необработанных обращений."""
        try:
            cur = await self._conn.execute(
                """SELECT COUNT(*) FROM support_messages WHERE status = 'new'""")
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            return 0

    async def get_support_messages(self, limit: int = 200, only_new: bool = False) -> list:
        """Читает обращения для разбора."""
        q = "SELECT telegram_id, username, full_name, text, created_at, status FROM support_messages"
        if only_new:
            q += " WHERE status = 'new'"
        q += " ORDER BY created_at DESC LIMIT ?"
        cur = await self._conn.execute(q, (limit,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def is_banned(self, telegram_id: int) -> bool:
        """Check if a user is banned."""
        user = await self.get_user(telegram_id)
        return user is not None and user["banned"] == 1

CREATE_POLL_ANSWERS_TABLE = """
CREATE TABLE IF NOT EXISTS poll_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id TEXT,
    telegram_id INTEGER,
    username TEXT,
    full_name TEXT,
    option_ids TEXT,
    created_at INTEGER
)
"""
