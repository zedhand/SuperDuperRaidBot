"""Tiny SQLite layer. Synchronous is fine for one guild's traffic."""
import json
import shutil
import time
import sqlite3
from datetime import date, datetime
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
CREATE TABLE IF NOT EXISTS series_signups (
    guild_id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    char_name TEXT NOT NULL,
    wow_class TEXT NOT NULL,
    spec TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (series_id, user_id)
);
CREATE TABLE IF NOT EXISTS cancelled_events (
    event_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    cancelled_ts INTEGER NOT NULL,
    cancelled_by INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    signups_json TEXT NOT NULL
);
"""

KEEP_CANCELLED_DAYS = 14  # /raid restore can bring a cancelled raid back for this long

# Columns added after v1.0: (table, column, definition). Each is added only if it is missing, so a real
# error (a locked or damaged file) is never mistaken for "already there". Older databases keep their data.
MIGRATIONS = (
    ("events", "lock_shown", "INTEGER NOT NULL DEFAULT 0"),   # v1.2
    ("signups", "bench_from", "TEXT"),                        # v1.5: set when auto-benched
    ("events", "repeat_days", "INTEGER NOT NULL DEFAULT 0"),  # v1.7: recurring raids
    ("events", "repeated", "INTEGER NOT NULL DEFAULT 0"),     # v1.7: next one already posted
    ("events", "thread_id", "INTEGER NOT NULL DEFAULT 0"),    # v1.7: discussion thread
    ("events", "archived", "INTEGER NOT NULL DEFAULT 0"),     # v1.7: post deleted by cleanup
    ("events", "nudged", "INTEGER NOT NULL DEFAULT 0"),       # v1.8: "who hasn't answered" ping sent
    ("signups", "no_show", "INTEGER NOT NULL DEFAULT 0"),     # v1.8: signed up but didn't turn up
    ("events", "autoclosed", "INTEGER NOT NULL DEFAULT 0"),   # v1.9: the automatic sign-up closing has run
    ("events", "series_id", "INTEGER NOT NULL DEFAULT 0"),    # v1.10: which repeating series a raid belongs to
    ("events", "voice_checked", "INTEGER NOT NULL DEFAULT 0"),  # v1.11: the voice-channel attendance check has run
    ("events", "class_caps", "TEXT NOT NULL DEFAULT ''"),     # v1.11: per-class limits, e.g. "Mage:4,Warlock:2"
    ("templates", "class_caps", "TEXT NOT NULL DEFAULT ''"),  # v1.11
)


def caps_dict(text: str | None) -> dict[str, int]:
    """'Mage:4,Warlock:2' -> {'Mage': 4, 'Warlock': 2} (per-class limits stored on a raid)."""
    out = {}
    for part in (text or "").split(","):
        name, _, n = part.partition(":")
        if name.strip() and n.strip().isdigit() and int(n) > 0:
            out[name.strip()] = int(n)
    return out


def caps_text(caps: dict[str, int]) -> str:
    return ",".join(f"{k}:{v}" for k, v in sorted(caps.items()) if v > 0)


def waiting_key(event, signups, s) -> str:
    """What a benched person is waiting for: their role ('tank', 'healer', 'dps') if that role is full,
    else their class if the raid limits that class. Used for places in line."""
    attending = [a for a in signups if a["status"] in ("in", "late")]
    role = s["role"]
    cap = event[f"cap_{role}"]
    if cap and sum(1 for a in attending if a["role"] == role) >= cap:
        return role
    caps = caps_dict(event["class_caps"])
    cls = s["wow_class"]
    if cls in caps and sum(1 for a in attending if a["wow_class"] == cls) >= caps[cls]:
        return cls
    return role


class DatabaseCorrupt(Exception):
    """The database file is damaged (not just busy or read-only)."""


def file_is_healthy(path) -> bool:
    """True if `path` opens as a SQLite database and passes SQLite's own consistency check."""
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


class DB:
    def __init__(self, path: str = "raidbot.db"):
        self.path = Path(path).resolve()
        self.recovered_from = None  # set by open_database() when it had to restore a backup
        self.conn = sqlite3.connect(path)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self.conn.execute("PRAGMA foreign_keys = ON")
            if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise DatabaseCorrupt("SQLite's consistency check failed")
            self.conn.executescript(SCHEMA)
            for table, column, ddl in MIGRATIONS:
                if column not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            self.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
            self.conn.commit()
        except sqlite3.OperationalError:  # busy / locked / read-only: not corruption, so say so as it is
            self.conn.close()
            raise
        except sqlite3.DatabaseError as e:  # "file is not a database", "malformed", ...
            self.conn.close()
            raise DatabaseCorrupt(str(e)) from e
        except DatabaseCorrupt:
            self.conn.close()
            raise

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
                     cap_tank, cap_healer, cap_dps, repeat_days=0, class_caps=""):
        return self._run(
            "INSERT INTO events(guild_id,channel_id,title,description,start_ts,creator_id,"
            "cap_tank,cap_healer,cap_dps,repeat_days,class_caps) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (guild_id, channel_id, title, description, start_ts, creator_id,
             cap_tank, cap_healer, cap_dps, repeat_days, class_caps)).lastrowid

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

    # ---- cancelled raids (kept KEEP_CANCELLED_DAYS for /raid restore) ----
    def cancel_event(self, event_id, by_user, now=None):
        """Delete a raid but keep a copy of it and its signups so /raid restore can bring it back.
        Returns False if there was no such raid. Event ids are never reused (AUTOINCREMENT), so the copy
        can go back in under the same id."""
        now = int(time.time()) if now is None else now
        event = self.event(event_id)
        if not event:
            return False
        signups = [dict(r) for r in self.signups(event_id)]
        with self.conn:  # one transaction: the copy is saved and the raid deleted, or neither
            self.conn.execute(
                "INSERT OR REPLACE INTO cancelled_events(event_id,guild_id,channel_id,title,start_ts,cancelled_ts,"
                "cancelled_by,event_json,signups_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, event["guild_id"], event["channel_id"], event["title"], event["start_ts"], now, by_user,
                 json.dumps(dict(event)), json.dumps(signups)))
            self.conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        return True

    def cancelled_events(self, guild_id, now=None):
        """This server's raids that can still be restored, newest cancellation first."""
        now = int(time.time()) if now is None else now
        return self._all("SELECT * FROM cancelled_events WHERE guild_id=? AND cancelled_ts>? ORDER BY cancelled_ts DESC",
                         (guild_id, now - KEEP_CANCELLED_DAYS * 86400))

    def cancelled_event(self, event_id, now=None):
        now = int(time.time()) if now is None else now
        return self._one("SELECT * FROM cancelled_events WHERE event_id=? AND cancelled_ts>?",
                         (event_id, now - KEEP_CANCELLED_DAYS * 86400))

    def restore_event(self, event_id):
        """Put a cancelled raid and its signups back. The old post is gone, so message_id and thread_id start at 0
        and the caller posts a new one. Returns the number of signups restored, or None if there is no copy."""
        row = self._one("SELECT * FROM cancelled_events WHERE event_id=?", (event_id,))
        if not row or self.event(event_id):
            return None
        event = json.loads(row["event_json"])
        event.update(message_id=0, thread_id=0, archived=0)
        signups = json.loads(row["signups_json"])
        ev_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(events)")}
        su_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signups)")}
        with self.conn:
            # column names come from the table itself (PRAGMA table_info), values are bound parameters
            cols = [c for c in event if c in ev_cols]
            self.conn.execute(f"INSERT INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))})",  # nosec B608
                              [event[c] for c in cols])
            for s in signups:
                cols = [c for c in s if c in su_cols]
                self.conn.execute(f"INSERT INTO signups({','.join(cols)}) VALUES({','.join('?' * len(cols))})",  # nosec B608
                                  [s[c] for c in cols])
            self.conn.execute("DELETE FROM cancelled_events WHERE event_id=?", (event_id,))
        return len(signups)

    def undo_restore(self, copy):
        """Take back a restore whose new post couldn't be sent: delete the raid again and put the original copy
        (a row from cancelled_events, with the old thread and post ids) back exactly as it was."""
        with self.conn:
            self.conn.execute("DELETE FROM events WHERE id=?", (copy["event_id"],))
            self.conn.execute(f"INSERT OR REPLACE INTO cancelled_events({','.join(copy.keys())}) "  # nosec B608
                              f"VALUES({','.join('?' * len(copy.keys()))})", tuple(copy))

    # ---- /forgetme ----
    USER_SETTING_KEYS = ("dm_remind",)  # per-player settings, stored as "<key>@<user id>"

    def user_data_counts(self, user_id):
        """What /forgetme would delete: {'characters', 'signups', 'series', 'settings', 'cancelled'} -> count."""
        uid = int(user_id)
        cancelled = sum(1 for r in self._all("SELECT signups_json FROM cancelled_events")
                        if any(int(s.get("user_id", 0)) == uid for s in json.loads(r["signups_json"])))
        return {
            "characters": self._one("SELECT COUNT(*) c FROM characters WHERE user_id=?", (uid,))["c"],
            "signups": self._one("SELECT COUNT(*) c FROM signups WHERE user_id=?", (uid,))["c"],
            "series": self._one("SELECT COUNT(*) c FROM series_signups WHERE user_id=?", (uid,))["c"],
            "settings": sum(self._one("SELECT COUNT(*) c FROM settings WHERE key=?", (f"{k}@{uid}",))["c"]
                            for k in self.USER_SETTING_KEYS),
            "cancelled": cancelled,
        }

    def forget_user(self, user_id):
        """Delete everything stored about one player, in every server: characters, signups (so attendance too),
        /always sign-ups, their own settings, and their signups inside the copies of cancelled raids.
        Raids they created stay (they belong to the server). One transaction: all of it goes, or none.
        Returns (counts, ids of raids they were signed up to) so the caller can redraw those posts."""
        uid = int(user_id)
        counts = self.user_data_counts(uid)
        event_ids = [r["event_id"] for r in self._all("SELECT event_id FROM signups WHERE user_id=?", (uid,))]
        with self.conn:
            self.conn.execute("DELETE FROM characters WHERE user_id=?", (uid,))
            self.conn.execute("DELETE FROM signups WHERE user_id=?", (uid,))
            self.conn.execute("DELETE FROM series_signups WHERE user_id=?", (uid,))
            self.conn.executemany("DELETE FROM settings WHERE key=?",
                                  [(f"{k}@{uid}",) for k in self.USER_SETTING_KEYS])
            for r in self.conn.execute("SELECT event_id, signups_json FROM cancelled_events").fetchall():
                signups = json.loads(r["signups_json"])
                kept = [s for s in signups if int(s.get("user_id", 0)) != uid]
                if len(kept) != len(signups):
                    self.conn.execute("UPDATE cancelled_events SET signups_json=? WHERE event_id=?",
                                      (json.dumps(kept), r["event_id"]))
        return counts, event_ids

    def purge_cancelled(self, now=None):
        """Forget cancelled raids older than KEEP_CANCELLED_DAYS. Returns how many were dropped."""
        now = int(time.time()) if now is None else now
        return self._run("DELETE FROM cancelled_events WHERE cancelled_ts<=?",
                         (now - KEEP_CANCELLED_DAYS * 86400,)).rowcount

    def pending_reminders(self, now):
        """Upcoming events not reminded yet. The caller applies each server's own lead time."""
        return self._all(
            "SELECT * FROM events WHERE reminded=0 AND closed=0 AND message_id!=0 AND start_ts > ?",
            (now,))

    def update_event(self, event_id, **fields):
        allowed = {"title", "description", "start_ts", "cap_tank", "cap_healer", "cap_dps", "reminded",
                   "lock_shown", "repeat_days", "repeated", "thread_id", "archived", "nudged", "autoclosed", "series_id", "voice_checked", "class_caps"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            # column names are filtered through the allow-list above, values are bound parameters
            self._run(f"UPDATE events SET {', '.join(k + '=?' for k in fields)} WHERE id=?",  # nosec B608
                      (*fields.values(), event_id))

    def pending_nudges(self, now):
        """Upcoming events whose 'who hasn't answered' ping hasn't gone out. Caller applies the setting."""
        return self._all("SELECT * FROM events WHERE nudged=0 AND closed=0 AND message_id!=0 AND start_ts>?", (now,))

    def pending_autoclose(self, now):
        """Upcoming, still-open events whose automatic sign-up closing hasn't run. Caller applies the setting."""
        return self._all("SELECT * FROM events WHERE autoclosed=0 AND closed=0 AND message_id!=0 AND start_ts>?", (now,))

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

    def count_class(self, event_id, wow_class, exclude_user):
        return self._one(
            "SELECT COUNT(*) c FROM signups WHERE event_id=? AND wow_class=? AND status IN ('in','late') "
            "AND user_id!=?", (event_id, wow_class, exclude_user))["c"]

    def pending_voicecheck(self, now, window=6 * 3600):
        """Raids that started in the last `window` seconds and haven't had their voice check. Caller applies the
        grace period and the settings."""
        return self._all("SELECT * FROM events WHERE voice_checked=0 AND message_id!=0 AND start_ts<=? AND start_ts>?",
                         (now, now - window))

    def raids_between(self, guild_id, since_ts, until_ts):
        return self._one("SELECT COUNT(*) c FROM events WHERE guild_id=? AND start_ts>=? AND start_ts<?",
                         (guild_id, since_ts, until_ts))["c"]

    def guilds_with_setting(self, key, value):
        return [r["guild_id"] for r in self._all("SELECT guild_id FROM settings WHERE key=? AND value=?", (key, value))]

    def last_character_used(self, user_id):
        """The character this person most recently signed up with (any raid), or None."""
        return self._one("SELECT char_name, wow_class, spec, role FROM signups WHERE user_id=? AND char_name IS NOT NULL "
                         "AND role IS NOT NULL ORDER BY updated_ts DESC, rowid DESC LIMIT 1", (user_id,))

    def bench_wait(self, event_id, user_id):
        """(place in line, what they wait for) for someone the bot benched because a role or class was full,
        first come first served; None if they aren't waiting for a slot. 'What' is 'tank'/'healer'/'dps' or a class."""
        me = self.signup(event_id, user_id)
        if not me or me["status"] != "bench" or not me["bench_from"] or not me["role"]:
            return None
        event, signups = self.event(event_id), self.signups(event_id)
        key = waiting_key(event, signups, me)
        line = [s["user_id"] for s in signups if s["status"] == "bench" and s["bench_from"] and s["role"]
                and waiting_key(event, signups, s) == key]
        return line.index(user_id) + 1, key

    def bench_position(self, event_id, user_id):
        """1-based place in line, or None if they aren't waiting for a slot."""
        found = self.bench_wait(event_id, user_id)
        return found[0] if found else None

    # ---- repeating-raid series ("always in") ----
    def join_series(self, guild_id, series_id, user_id, char_name, wow_class, spec, role):
        self._run("INSERT INTO series_signups(guild_id,series_id,user_id,char_name,wow_class,spec,role) "
                  "VALUES(?,?,?,?,?,?,?) ON CONFLICT(series_id,user_id) DO UPDATE SET char_name=excluded.char_name, "
                  "wow_class=excluded.wow_class, spec=excluded.spec, role=excluded.role",
                  (guild_id, series_id, user_id, char_name, wow_class, spec, role))

    def leave_series(self, series_id, user_id):
        return self._run("DELETE FROM series_signups WHERE series_id=? AND user_id=?", (series_id, user_id)).rowcount > 0

    def series_members(self, series_id):
        return self._all("SELECT * FROM series_signups WHERE series_id=? ORDER BY rowid", (series_id,))

    def user_series(self, guild_id, user_id):
        return self._all("SELECT * FROM series_signups WHERE guild_id=? AND user_id=? ORDER BY series_id",
                         (guild_id, user_id))

    def latest_in_series(self, series_id):
        return self._one("SELECT * FROM events WHERE series_id=? OR id=? ORDER BY start_ts DESC LIMIT 1",
                         (series_id, series_id))

    def upcoming_recurring(self, guild_id, since_ts):
        return self._all("SELECT * FROM events WHERE guild_id=? AND repeat_days>0 AND start_ts>=? AND message_id!=0 "
                         "ORDER BY start_ts", (guild_id, since_ts))

    # ---- templates ----
    def template(self, guild_id, name):
        return self._one("SELECT * FROM templates WHERE guild_id=? AND name=?", (guild_id, name))

    def templates(self, guild_id):
        return self._all("SELECT * FROM templates WHERE guild_id=? ORDER BY name", (guild_id,))

    def save_template(self, guild_id, name, title, description, cap_tank, cap_healer, cap_dps, repeat_days,
                      class_caps=""):
        self._run(
            "INSERT INTO templates(guild_id,name,title,description,cap_tank,cap_healer,cap_dps,repeat_days,class_caps) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, cap_tank=excluded.cap_tank, cap_healer=excluded.cap_healer, "
            "cap_dps=excluded.cap_dps, repeat_days=excluded.repeat_days, class_caps=excluded.class_caps",
            (guild_id, name, title, description, cap_tank, cap_healer, cap_dps, repeat_days, class_caps))

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
        The copy is written under a temporary name, checked, and only then renamed, so a crash or a
        bad copy can never be mistaken for today's backup. Returns the new file's path, or None if
        today's backup already exists. Raises if the new copy fails its consistency check."""
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"raidbot-{date.today().isoformat()}.db"
        if target.exists():
            return None
        tmp = folder / (target.name + ".tmp")
        tmp.unlink(missing_ok=True)
        dest = sqlite3.connect(tmp)
        try:
            self.conn.backup(dest)  # safe while the bot is running
        finally:
            dest.close()
        if not file_is_healthy(tmp):
            tmp.unlink(missing_ok=True)
            raise RuntimeError("the new backup failed its consistency check, so it was discarded")
        tmp.replace(target)
        for old in sorted(folder.glob("raidbot-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return target

    def snapshot_to(self, dest_path) -> None:
        """Write a consistent copy of the live database to dest_path (for /config backup)."""
        dest = sqlite3.connect(dest_path)
        try:
            self.conn.backup(dest)
        finally:
            dest.close()


def open_database(path: str = "raidbot.db") -> DB:
    """Open the database. If the file is damaged, put it aside and restore the newest healthy daily
    backup instead of starting with an empty or broken database. Raises SystemExit with a plain-English
    message when the file is damaged and there is no healthy backup (the file is then left untouched)."""
    try:
        return DB(path)
    except DatabaseCorrupt as e:
        problem = str(e)
    target = Path(path).resolve()
    good = next((b for b in sorted((target.parent / "backups").glob("raidbot-*.db"), reverse=True)
                 if file_is_healthy(b)), None)
    if good is None:
        raise SystemExit(f"The database file {target.name} is damaged ({problem}) and there is no healthy backup "
                         f"in the 'backups' folder to restore. The file was left untouched. Copy a backup "
                         f"into place by hand, or move {target.name} away to start fresh.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = target.with_name(f"{target.name}.damaged-{stamp}")
    target.replace(aside)
    for ext in ("-wal", "-shm", "-journal"):
        Path(str(target) + ext).unlink(missing_ok=True)
    shutil.copy2(good, target)
    database = DB(str(target))
    database.recovered_from = good.name
    return database
