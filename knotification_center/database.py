'''Database management for KDE Notification Center'''

from datetime import datetime, timedelta
from json import dumps, loads
from pathlib import Path
import sqlite3
from typing import Any, Optional

from knotification_center import constants
from knotification_center import util

LOGGER = util.LOGGER


class NotificationDB:
    '''Persistent SQLite storage for notifications with WAL mode and indices'''

    def __init__(self, db_path: Path | str = constants.DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_db()
        self.auto_purge_dismissed(days=constants.DEFAULT_PURGE_DAYS)

    def close(self) -> None:
        '''Close the persistent database connection'''

        self.conn.close()

    def _init_db(self) -> None:
        '''Initialize table schema and indices if not present'''

        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dbus_id INTEGER DEFAULT 0,
                    app_name TEXT NOT NULL,
                    app_icon TEXT,
                    summary TEXT,
                    body TEXT,
                    urgency INTEGER DEFAULT 1,
                    category TEXT,
                    actions_json TEXT,
                    hints_json TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    dismissed_at TIMESTAMP
                );
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_status ON notifications(status);"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_name ON notifications(app_name);"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON notifications(created_at);"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dbus_id ON notifications(dbus_id);"
            )

    def insert_notification(
        self,
        app_name : str,
        summary  : str,
        body     : str,
        app_icon : str = "",
        urgency  : int = constants.Urgency.NORMAL,
        category : str = "",
        actions  : Optional[list[str]] = None,
        hints    : Optional[dict[str, Any]] = None,
        dbus_id  : int = 0,
    ) -> int:
        '''Insert a new notification and return its internal ID'''

        actions_json = dumps(actions or list())
        clean_hints: dict[str, Any] = dict()
        if hints:
            for k, v in hints.items():
                if isinstance(v, (str, int, float, bool, list, dict)):
                    clean_hints[str(k)] = v
                else:
                    clean_hints[str(k)] = str(v)
        hints_json = dumps(clean_hints)

        LOGGER.debug(
            "Inserting notification: app=%s, summary=%s, dbus_id=%d",
            app_name,
            summary,
            dbus_id,
        )
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO notifications (
                    dbus_id, app_name, app_icon, summary, body,
                    urgency, category, actions_json, hints_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
                """,
                (
                    dbus_id,
                    app_name,
                    app_icon,
                    summary,
                    body,
                    int(urgency),
                    category,
                    actions_json,
                    hints_json,
                ),
            )
            return cursor.lastrowid or 0

    def update_dbus_id(self, internal_id: int, dbus_id: int) -> None:
        '''Associate an internal notification record with its external D-Bus ID'''

        LOGGER.debug("Updating notification ID %d with D-Bus ID %d", internal_id, dbus_id)
        with self.conn:
            self.conn.execute(
                "UPDATE notifications SET dbus_id = ? WHERE id = ?",
                (dbus_id, internal_id),
            )

    def mark_dismissed_by_dbus_id(self, dbus_id: int) -> None:
        '''Mark an active notification as dismissed via its D-Bus ID'''

        if not dbus_id:
            return
        LOGGER.debug("Marking dismissed by D-Bus ID: %d", dbus_id)
        with self.conn:
            self.conn.execute(
                """
                UPDATE notifications
                SET status = 'dismissed', dismissed_at = CURRENT_TIMESTAMP
                WHERE dbus_id = ? AND status = 'active'
                """,
                (dbus_id,),
            )

    def mark_dismissed_by_id(self, internal_id: int) -> Optional[int]:
        '''Mark notification dismissed by internal ID and return its D-Bus ID if active'''

        LOGGER.debug("Marking dismissed by internal ID: %d", internal_id)
        with self.conn:
            row = self.conn.execute(
                "SELECT dbus_id, status FROM notifications WHERE id = ?",
                (internal_id,),
            ).fetchone()
            self.conn.execute(
                """
                UPDATE notifications
                SET status = 'dismissed', dismissed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (internal_id,),
            )
            if row and row["status"] == constants.Status.ACTIVE.value and row["dbus_id"]:
                return row["dbus_id"]
            return None

    def mark_multiple_dismissed(self, internal_ids: list[int]) -> list[int]:
        '''Mark multiple notifications dismissed and return their active D-Bus IDs'''

        if not internal_ids:
            return list()
        placeholders = ",".join("?" for _ in internal_ids)
        LOGGER.debug("Marking multiple dismissed: %s", internal_ids)
        with self.conn:
            rows = self.conn.execute(
                f"SELECT dbus_id FROM notifications WHERE id IN ({placeholders}) AND status = 'active'",
                internal_ids,
            ).fetchall()
            dbus_ids = [r["dbus_id"] for r in rows if r["dbus_id"]]
            self.conn.execute(
                f"""
                UPDATE notifications
                SET status = 'dismissed', dismissed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                internal_ids,
            )
            return dbus_ids

    def clear_all_active(self) -> list[int]:
        '''Mark all active notifications as dismissed and return their D-Bus IDs'''

        LOGGER.debug("Clearing all active notifications in database")
        with self.conn:
            rows = self.conn.execute(
                "SELECT dbus_id FROM notifications WHERE status = 'active'"
            ).fetchall()
            dbus_ids = [r["dbus_id"] for r in rows if r["dbus_id"]]
            self.conn.execute(
                """
                UPDATE notifications
                SET status = 'dismissed', dismissed_at = CURRENT_TIMESTAMP
                WHERE status = 'active'
                """
            )
            return dbus_ids

    def delete_by_id(self, internal_id: int) -> Optional[int]:
        '''Permanently delete notification from database and return D-Bus ID if active'''

        LOGGER.debug("Deleting notification ID %d", internal_id)
        with self.conn:
            row = self.conn.execute(
                "SELECT dbus_id, status FROM notifications WHERE id = ?",
                (internal_id,),
            ).fetchone()
            self.conn.execute("DELETE FROM notifications WHERE id = ?", (internal_id,))
            if row and row["status"] == constants.Status.ACTIVE.value and row["dbus_id"]:
                return row["dbus_id"]
            return None

    def delete_multiple(self, internal_ids: list[int]) -> list[int]:
        '''Permanently delete notifications and return active D-Bus IDs'''

        if not internal_ids:
            return list()
        placeholders = ",".join("?" for _ in internal_ids)
        LOGGER.debug("Deleting multiple notifications: %s", internal_ids)
        with self.conn:
            rows = self.conn.execute(
                f"SELECT dbus_id FROM notifications WHERE id IN ({placeholders}) AND status = 'active'",
                internal_ids,
            ).fetchall()
            active_dbus_ids = [r["dbus_id"] for r in rows if r["dbus_id"]]
            self.conn.execute(
                f"DELETE FROM notifications WHERE id IN ({placeholders})",
                internal_ids,
            )
            return active_dbus_ids

    def auto_purge_dismissed(self, days: int = constants.DEFAULT_PURGE_DAYS) -> int:
        '''Purge dismissed notifications older than specified days'''

        cutoff = datetime.now() - timedelta(days=days)
        LOGGER.debug(
            "Auto-purging dismissed notifications older than %d days (%s)",
            days,
            cutoff,
        )
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM notifications WHERE created_at < ? AND status = 'dismissed'",
                (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
            )
            return cursor.rowcount

    def purge_history(self, days: int = constants.DEFAULT_PURGE_DAYS) -> int:
        '''Purge history convenience alias'''

        return self.auto_purge_dismissed(days=days)

    def query_notifications(
        self,
        search_text    : str           = "",
        app_filter     : str           = "",
        status_filter  : str           = constants.Status.ACTIVE.value,
        urgency_filter : Optional[int] = None,
        limit          : int           = constants.QUERY_LIMIT_DEFAULT,
    ) -> list[dict[str, Any]]:
        '''Query notifications with optional search text, app, status, and urgency filters'''

        query = "SELECT * FROM notifications WHERE 1=1"
        params: list[Any] = list()

        if status_filter and status_filter != "all":
            query += " AND status = ?"
            params.append(status_filter)

        if app_filter and app_filter not in ("", "all", "All", "All Apps", "All Applications"):
            query += " AND app_name = ?"
            params.append(app_filter)

        if urgency_filter is not None and urgency_filter >= 0:
            query += " AND urgency = ?"
            params.append(urgency_filter)

        if search_text:
            wildcard = f"%{search_text}%"
            query += " AND (summary LIKE ? OR body LIKE ? OR app_name LIKE ?)"
            params.extend([wildcard, wildcard, wildcard])

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        results: list[dict[str, Any]] = list()
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["actions"] = loads(d["actions_json"]) if d["actions_json"] else list()
            except Exception:
                d["actions"] = list()
            try:
                d["hints"] = loads(d["hints_json"]) if d["hints_json"] else dict()
            except Exception:
                d["hints"] = dict()
            results.append(d)
        return results

    def get_distinct_apps(self) -> list[str]:
        '''Return list of distinct application names in notification history'''

        cursor = self.conn.execute(
            "SELECT DISTINCT app_name FROM notifications WHERE app_name != '' ORDER BY app_name ASC"
        )
        return [row["app_name"] for row in cursor.fetchall()]

    def get_counts(self) -> dict[str, int]:
        '''Return counts for total, active, and dismissed notifications'''

        total     = self.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        active    = self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE status = 'active'"
        ).fetchone()[0]
        dismissed = self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE status = 'dismissed'"
        ).fetchone()[0]
        return {
            "total"    : total,
            "active"   : active,
            "dismissed": dismissed,
        }
