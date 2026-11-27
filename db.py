"""SQLite storage: goals -> habits -> daily checkins."""
import sqlite3
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS goal (
    id       INTEGER PRIMARY KEY,
    user_id  INTEGER NOT NULL DEFAULT 0,
    title    TEXT    NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS habit (
    id       INTEGER PRIMARY KEY,
    goal_id  INTEGER NOT NULL REFERENCES goal(id),
    title    TEXT    NOT NULL,
    days     INTEGER NOT NULL DEFAULT 127, -- bitmask, bit 0 = Monday
    created  TEXT    NOT NULL,             -- YYYY-MM-DD in user's TZ
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checkin (
    habit_id INTEGER NOT NULL REFERENCES habit(id),
    date     TEXT    NOT NULL,
    PRIMARY KEY (habit_id, date)
);
"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(goal)")}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE goal ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    return conn


def add_goal(conn, user_id, title):
    with conn:
        return conn.execute("INSERT INTO goal(user_id, title) VALUES (?, ?)", (user_id, title)).lastrowid


def goals(conn, user_id):
    return conn.execute("SELECT * FROM goal WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,)).fetchall()


def archive_goal(conn, user_id, goal_id):
    with conn:
        return conn.execute("UPDATE goal SET archived = 1 WHERE id = ? AND user_id = ?", (goal_id, user_id)).rowcount > 0


def add_habit(conn, goal_id, title, created: date):
    with conn:
        return conn.execute(
            "INSERT INTO habit(goal_id, title, created) VALUES (?, ?, ?)",
            (goal_id, title, created.isoformat()),
        ).lastrowid


def get_habit(conn, habit_id):
    return conn.execute(
        "SELECT h.*, g.user_id FROM habit h JOIN goal g ON g.id = h.goal_id WHERE h.id = ?",
        (habit_id,),
    ).fetchone()


def habits(conn, goal_id):
    return conn.execute(
        "SELECT * FROM habit WHERE goal_id = ? AND archived = 0 ORDER BY id", (goal_id,)
    ).fetchall()


def archive_habit(conn, user_id, habit_id):
    with conn:
        return conn.execute(
            "UPDATE habit SET archived = 1 WHERE id = ? AND goal_id IN (SELECT id FROM goal WHERE user_id = ?)",
            (habit_id, user_id),
        ).rowcount > 0


def toggle_day(conn, habit_id, weekday):
    """Flip one weekday in the schedule. Refuses to leave a habit with no days."""
    days = get_habit(conn, habit_id)["days"] ^ (1 << weekday)
    if not days:
        return False
    with conn:
        conn.execute("UPDATE habit SET days = ? WHERE id = ?", (days, habit_id))
    return True


def today_habits(conn, user_id, day: date):
    """[(habit_id, title, done)] scheduled for `day`."""
    d = day.isoformat()
    rows = conn.execute(
        """SELECT h.id, h.title, h.days, c.habit_id IS NOT NULL AS done
           FROM habit h
           JOIN goal g ON g.id = h.goal_id
           LEFT JOIN checkin c ON c.habit_id = h.id AND c.date = ?
           WHERE g.user_id = ? AND h.archived = 0 AND g.archived = 0 AND h.created <= ?
           ORDER BY g.id, h.id""",
        (d, user_id, d),
    ).fetchall()
    return [(r["id"], r["title"], bool(r["done"])) for r in rows if r["days"] >> day.weekday() & 1]


def toggle_checkin(conn, habit_id, day: date):
    """Mark/unmark a habit for `day`. Returns new state."""
    d = day.isoformat()
    with conn:
        if conn.execute("DELETE FROM checkin WHERE habit_id = ? AND date = ?", (habit_id, d)).rowcount:
            return False
        conn.execute("INSERT INTO checkin VALUES (?, ?)", (habit_id, d))
        return True


def week_rate(conn, habit_id, end: date):
    """(done, scheduled) over the 7 days ending at `end`, ignoring days before the habit existed."""
    h = get_habit(conn, habit_id)
    created = date.fromisoformat(h["created"])
    sched = [
        d.isoformat()
        for d in (end - timedelta(i) for i in range(7))
        if d >= created and h["days"] >> d.weekday() & 1
    ]
    if not sched:
        return 0, 0
    done = conn.execute(
        f"SELECT COUNT(*) FROM checkin WHERE habit_id = ? AND date IN ({','.join('?' * len(sched))})",
        (habit_id, *sched),
    ).fetchone()[0]
    return done, len(sched)
