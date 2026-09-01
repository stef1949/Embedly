from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_SQLITE_INTEGER = (1 << 63) - 1
_MESSAGE_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class MessageOwnership:
    message_id: int
    channel_id: int
    guild_id: int | None
    original_author_id: int
    message_type: str
    details: str | None = None
    transcript: str | None = None


class SQLiteStateStore:
    """Persist bot-message ownership and TikTok callback data.

    Ownership is keyed by Discord-issued message, channel and guild IDs. The
    short operations are protected by a lock so callbacks and message handlers
    can safely share one connection.
    """

    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        raw_path = os.fspath(database_path)
        if not raw_path:
            raise ValueError("database_path must not be empty")

        if raw_path != ":memory:":
            path = Path(raw_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            raw_path = os.fspath(path)

        self.database_path = raw_path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            raw_path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if raw_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS message_ownership (
                    message_id INTEGER PRIMARY KEY CHECK (message_id > 0),
                    channel_id INTEGER NOT NULL CHECK (channel_id > 0),
                    guild_id INTEGER CHECK (guild_id > 0),
                    original_author_id INTEGER NOT NULL CHECK (original_author_id > 0),
                    message_type TEXT NOT NULL,
                    details TEXT,
                    transcript TEXT,
                    created_at INTEGER NOT NULL CHECK (created_at >= 0)
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_message_ownership(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: int | None,
        original_author_id: int,
        message_type: str,
        details: str | None = None,
        transcript: str | None = None,
    ) -> None:
        if not _MESSAGE_TYPE_PATTERN.fullmatch(message_type):
            raise ValueError("message_type must be a short lower-case identifier")

        values = (
            _positive_id(message_id, "message_id"),
            _positive_id(channel_id, "channel_id"),
            _optional_positive_id(guild_id, "guild_id"),
            _positive_id(original_author_id, "original_author_id"),
            message_type,
            _optional_text(details, "details", 4000),
            _optional_text(transcript, "transcript", 4000),
            int(time.time()),
        )

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO message_ownership(
                        message_id, channel_id, guild_id, original_author_id,
                        message_type, details, transcript, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        guild_id = excluded.guild_id,
                        original_author_id = excluded.original_author_id,
                        message_type = excluded.message_type,
                        details = excluded.details,
                        transcript = excluded.transcript,
                        created_at = excluded.created_at
                    WHERE message_ownership.channel_id = excluded.channel_id
                      AND message_ownership.guild_id IS excluded.guild_id
                      AND message_ownership.original_author_id = excluded.original_author_id
                      AND message_ownership.message_type = excluded.message_type
                    """,
                    values,
                )
                row = self._connection.execute(
                    """
                    SELECT channel_id, guild_id, original_author_id, message_type
                    FROM message_ownership WHERE message_id = ?
                    """,
                    (values[0],),
                ).fetchone()
                expected = (values[1], values[2], values[3], values[4])
                actual = (
                    row["channel_id"],
                    row["guild_id"],
                    row["original_author_id"],
                    row["message_type"],
                )
                if actual != expected:
                    raise RuntimeError("message ownership record conflicts with existing state")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def get_message_ownership(
        self,
        message_id: int,
        channel_id: int,
        guild_id: int | None,
    ) -> MessageOwnership | None:
        values = (
            _positive_id(message_id, "message_id"),
            _positive_id(channel_id, "channel_id"),
            _optional_positive_id(guild_id, "guild_id"),
        )
        with self._lock:
            row = self._connection.execute(
                """
                SELECT message_id, channel_id, guild_id, original_author_id,
                       message_type, details, transcript
                FROM message_ownership
                WHERE message_id = ? AND channel_id = ? AND guild_id IS ?
                """,
                values,
            ).fetchone()
        if row is None:
            return None
        return MessageOwnership(
            message_id=int(row["message_id"]),
            channel_id=int(row["channel_id"]),
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            original_author_id=int(row["original_author_id"]),
            message_type=str(row["message_type"]),
            details=str(row["details"]) if row["details"] is not None else None,
            transcript=str(row["transcript"]) if row["transcript"] is not None else None,
        )

    def delete_message_ownership(self, message_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM message_ownership WHERE message_id = ?",
                (_positive_id(message_id, "message_id"),),
            )
        return cursor.rowcount > 0

    def prune_message_ownership(self, older_than_timestamp: int) -> int:
        if isinstance(older_than_timestamp, bool) or not isinstance(older_than_timestamp, int):
            raise ValueError("older_than_timestamp must be an integer")
        if older_than_timestamp < 0:
            raise ValueError("older_than_timestamp must not be negative")
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM message_ownership WHERE created_at < ?",
                (older_than_timestamp,),
            )
        return cursor.rowcount


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value <= 0 or value > _MAX_SQLITE_INTEGER:
        raise ValueError(f"{name} must be a positive Discord ID")
    return value


def _optional_positive_id(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    return _positive_id(value, name)


def _optional_text(value: str | None, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return value
