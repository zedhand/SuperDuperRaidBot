"""Tiny SQLite layer. Synchronous is fine for one guild's traffic."""
import sqlite3
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    wow_class TEXT NOT NULL,
    spec TEXT NOT NULL,
    role TEXT NOT NULL,
    is_main INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_ts INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    cap_tank INTEGER NOT NULL DEFAULT 0,
    cap_healer INTEGER NOT NULL DEFAULT 0,
    cap_dps INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0,
    reminded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS signups (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    char_name TEXT,
    wow_class TEXT,
    spec TEXT,
    role TEXT,
    status TEXT NOT NULL,
    updated_ts INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS templates (
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cap_tank INTEGER NOT NULL DEFAULT 0,
    cap_healer INTEGER NOT NULL DEFAULT 0,
    cap_dps INTEGER NOT NULL DEFAULT 0,
    repeat_days INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, name)
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (guild_id, key)
);
"""

# Columns added after v1.0. Older databases get them on startup; the data is kept.
MIGRATIONS = (
    "ALTER TABLE events ADD COLUMN lock_shown INTEGER NOT NULL DEFAULT 0",   # v1.2
    "ALTER TABLE signups ADD COLUMN bench_from TEXT",                        # v1.5: set when auto-benched
    "ALTER TABLE events ADD COLUMN repeat_days INTEGER NOT NULL DEFAULT 0",  # v1.7: recurring raids
    "ALTER TABLE events ADD COLUMN repeated INTEGER NOT NULL DEFAULT 0",     # v1.7: next one already posted
    "ALTER TABLE events ADD COLUMN thread_id INTEGER NOT NULL DEFAULT 0",    # v1.7: discussion thread
    "ALTER TABLE events ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",     # v1.7: post deleted by cleanup
    "ALTER TABLE events ADD COLUMN nudged INTEGER NOT NULL DEFAULT 0",       # v1.8: "who hasn't answered" ping sent
    "ALTER TABLE signups ADD COLUMN no_show INTEGER NOT NULL DEFAULT 0",     # v1.8: signed up but didn't turn up
)


class DB:
    def __init__(self, path: str = "raidbot.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                self.conn.execute(sql)
                self.conn.commit()
            except sqlite3.OperationalError:  # column already exists
                pass
        self.path = Path(path).resolve()

    def _all(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def _run(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    # ---- characters ----
    def characters(self, user_id):
        return self._all("SELECT * FROM characters WHERE user_id=? ORDER BY is_main DESC, name", (user_id,))

    def character(self, char_id, user_id):
        return self._one("SELECT * FROM characters WHERE id=? AND user_id=?", (char_id, user_id))

    def character_by_name(self, user_id, name):
        return self._one("SELECT * FROM characters WHERE user_id=? AND name=?", (user_id, name))

    def main_character(self, user_id):
        return self._one("SELECT * FROM characters WHERE user_id=? ORDER BY is_main DESC, id LIMIT 1", (user_id,))

    def upsert_character(self, user_id, name, wow_class, spec, role, make_main):
        has_any = self._one("SELECT 1 FROM characters WHERE user_id=?", (user_id,)) is not None
        existing = self.character_by_name(user_id, name)
        if existing:
            self._run("UPDATE characters SET wow_class=?, spec=?, role=? WHERE id=?",
                      (wow_class, spec, role, existing["id"]))
            char_id = existing["id"]
        else:
            char_id = self._run(
                "INSERT INTO characters(user_id,name,wow_class,spec,role,is_main) VALUES(?,?,?,?,?,0)",
                (user_id, name, wow_class, spec, role)).lastrowid
        if make_main or not has_any:
            self.set_main(user_id, char_id)
        return char_id

    def set_main(self, user_id, char_id):
        self._run("UPDATE characters SET is_main=0 WHERE user_id=?", (user_id,))
        self._run("UPDATE characters SET is_main=1 WHERE id=? AND user_id=?", (char_id, user_id))

    def delete_character(self, user_id, char_id):
        self._run("DELETE FROM characters WHERE id=? AND user_id=?", (char_id, user_id))
        if self.main_character(user_id) and not self._one(
                "SELECT 1 FROM characters WHERE user_id=? AND is_main=1", (user_id,)):
            self.set_main(user_id, self.main_character(user_id)["id"])

    # ---- events ----
    def create_event(self, guild_id, channel_id, title, description, start_ts, creator_id,
                     cap_tank, cap_healer, cap_dps, repeat_days=0):
        return self._run(
            "INSERT INTO events(guild_id,channel_id,title,description,start_ts,creator_id,"
            "cap_tank,cap_healer,cap_dps,repeat_days) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (guild_id, channel_id, title, description, start_ts, creator_id,
             cap_tank, cap_healer, cap_dps, repeat_days)).lastrowid

    def set_message_id(self, event_id, message_id):
        self._run("UPDATE events SET message_id=? WHERE id=?", (message_id, event_id))

    def event(self, event_id):
        return self._one("SELECT * FROM events WHERE id=?", (event_id,))

    def event_by_message(self, message_id):
        return self._one("SELECT * FROM events WHERE message_id=?", (message_id,))

    def upcoming_events(self, guild_id, since_ts):
        return self._all("SELECT * FROM events WHERE guild_id=? AND start_ts>=? ORDER BY start_ts",
                         (guild_id, since_ts))

    def set_closed(self, event_id, closed):
        self._run("UPDATE events SET closed=? WHERE id=?", (int(closed), event_id))

    def delete_event(self, event_id):
        self._run("DELETE FROM events WHERE id=?", (event_id,))

    def pending_reminders(self, now):
        """Upcoming events not reminded yet. The caller applies each server's own lead time."""
        return self._all(
            "SELECT * FROM events WHERE reminded=0 AND closed=0 AND message_id!=0 AND start_ts > ?",
            (now,))

    def update_event(self, event_id, **fields):
        allowed = {"title", "description", "start_ts", "cap_tank", "cap_healer", "cap_dps", "reminded",
                   "lock_shown", "repeat_days", "repeated", "thread_id", "archived", "nudged"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            self._run(f"UPDATE events SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                      (*fields.values(), event_id))

    def pending_nudges(self, now):
        """Upcoming events whose 'who hasn't answered' ping hasn't gone out. Caller applies the setting."""
        return self._all("SELECT * FROM events WHERE nudged=0 AND closed=0 AND message_id!=0 AND start_ts>?", (now,))

    def due_repeat(self, now):
        """Recurring raids that have started but haven't posted their next occurrence yet."""
        return self._all("SELECT * FROM events WHERE repeat_days>0 AND repeated=0 AND message_id!=0 "
                         "AND start_ts<=?", (now,))

    def archivable(self, before_ts):
        """Posts that started before before_ts and are still up. The caller applies each server's setting."""
        return self._all("SELECT * FROM events WHERE archived=0 AND message_id!=0 AND start_ts<?", (before_ts,))

    def mark_reminded(self, event_id):
        self._run("UPDATE events SET reminded=1 WHERE id=?", (event_id,))

    def due_lock(self, now):
        """Events whose start time has passed but whose post still shows open buttons."""
        return self._all("SELECT * FROM events WHERE lock_shown=0 AND message_id!=0 AND start_ts<=?", (now,))

    def mark_lock_shown(self, event_id):
        self._run("UPDATE events SET lock_shown=1 WHERE id=?", (event_id,))

    # ---- signups ----
    def signups(self, event_id):
        # rowid = order of first signup; it doesn't change when someone edits their status
        return self._all("SELECT * FROM signups WHERE event_id=? ORDER BY rowid", (event_id,))

    def signup(self, event_id, user_id):
        return self._one("SELECT * FROM signups WHERE event_id=? AND user_id=?", (event_id, user_id))

    def count_role(self, event_id, role, exclude_user):
        return self._one(
            "SELECT COUNT(*) c FROM signups WHERE event_id=? AND role=? AND status IN ('in','late') "
            "AND user_id!=?", (event_id, role, exclude_user))["c"]

    def set_signup(self, event_id, user_id, char_name, wow_class, spec, role, status, bench_from=None):
        """bench_from: 'in'/'late' when the bot benched them because the role was full (so they can
        be promoted automatically later); None for any choice the player made themselves."""
        self._run(
            "INSERT INTO signups(event_id,user_id,char_name,wow_class,spec,role,status,bench_from) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id,user_id) DO UPDATE SET "
            "char_name=excluded.char_name, wow_class=excluded.wow_class, spec=excluded.spec, "
            "role=excluded.role, status=excluded.status, bench_from=excluded.bench_from, "
            "updated_ts=strftime('%s','now')",
            (event_id, user_id, char_name, wow_class, spec, role, status, bench_from))

    def set_no_show(self, event_id, user_id, flag):
        return self._run("UPDATE signups SET no_show=? WHERE event_id=? AND user_id=?",
                         (int(flag), event_id, user_id)).rowcount > 0

    def stats(self, guild_id, since_ts, until_ts, user_id=None):
        """Attendance per person over raids that have already started in [since_ts, until_ts)."""
        sql = ("SELECT s.user_id, COUNT(*) AS total, "
               "SUM(CASE WHEN s.status IN ('in','late') AND s.no_show=0 THEN 1 ELSE 0 END) AS attended, "
               "SUM(CASE WHEN s.status='late' AND s.no_show=0 THEN 1 ELSE 0 END) AS late, "
               "SUM(s.no_show) AS no_show, "
               "SUM(CASE WHEN s.status='absent' THEN 1 ELSE 0 END) AS absent "
               "FROM signups s JOIN events e ON e.id=s.event_id "
               "WHERE e.guild_id=? AND e.start_ts>=? AND e.start_ts<?")
        args = [guild_id, since_ts, until_ts]
        if user_id is not None:
            sql += " AND s.user_id=?"
            args.append(user_id)
        sql += " GROUP BY s.user_id ORDER BY attended DESC, no_show ASC, s.user_id"
        return self._all(sql, args)

    def user_upcoming(self, guild_id, user_id, now):
        return self._all(
            "SELECT e.*, s.status AS my_status FROM signups s JOIN events e ON e.id=s.event_id "
            "WHERE e.guild_id=? AND s.user_id=? AND e.start_ts>=? AND e.message_id!=0 ORDER BY e.start_ts",
            (guild_id, user_id, now))

    def remove_signup(self, event_id, user_id):
        self._run("DELETE FROM signups WHERE event_id=? AND user_id=?", (event_id, user_id))

    # ---- templates ----
    def template(self, guild_id, name):
        return self._one("SELECT * FROM templates WHERE guild_id=? AND name=?", (guild_id, name))

    def templates(self, guild_id):
        return self._all("SELECT * FROM templates WHERE guild_id=? ORDER BY name", (guild_id,))

    def save_template(self, guild_id, name, title, description, cap_tank, cap_healer, cap_dps, repeat_days):
        self._run(
            "INSERT INTO templates(guild_id,name,title,description,cap_tank,cap_healer,cap_dps,repeat_days) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, cap_tank=excluded.cap_tank, cap_healer=excluded.cap_healer, "
            "cap_dps=excluded.cap_dps, repeat_days=excluded.repeat_days",
            (guild_id, name, title, description, cap_tank, cap_healer, cap_dps, repeat_days))

    def delete_template(self, guild_id, name):
        return self._run("DELETE FROM templates WHERE guild_id=? AND name=?", (guild_id, name)).rowcount > 0

    # ---- settings (per server) ----
    def setting(self, guild_id, key, default=None):
        row = self._one("SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key))
        return row["value"] if row else default

    def set_setting(self, guild_id, key, value):
        if value is None:
            self._run("DELETE FROM settings WHERE guild_id=? AND key=?", (guild_id, key))
        else:
            self._run("INSERT INTO settings(guild_id,key,value) VALUES(?,?,?) "
                      "ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
                      (guild_id, key, str(value)))

    def settings_with_prefix(self, guild_id, prefix=""):
        """{key: value} for every setting of a server whose key starts with prefix."""
        return {r["key"]: r["value"] for r in
                self._all("SELECT key, value FROM settings WHERE guild_id=?", (guild_id,))
                if r["key"].startswith(prefix)}

    # ---- backups ----
    def backup_daily(self, keep: int = 7):
        """Copy the database to backups/raidbot-YYYY-MM-DD.db (once a day); keep the newest `keep`.
        Returns the new file's path, or None if today's backup already exists."""
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"raidbot-{date.today().isoformat()}.db"
        if target.exists():
            return None
        dest = sqlite3.connect(target)
        try:
            self.conn.backup(dest)  # safe while the bot is running
        finally:
            dest.close()
        for old in sorted(folder.glob("raidbot-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return target
