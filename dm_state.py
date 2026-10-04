#!/usr/bin/env python3
"""The DM's own memory: one SQLite file, separate from the game databases.

It holds what the game does not: who the Overseer has noticed, what each
character has done, every bounty it has issued and how that went, and the
running story for each character. A world rebuild does not touch it.

    DM_STATE   path to the file (default: state.db beside this script)
"""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    guid INTEGER PRIMARY KEY, name TEXT, race INTEGER, class INTEGER, level INTEGER,
    zone INTEGER, first_seen INTEGER, last_seen INTEGER,
    story_so_far TEXT NOT NULL DEFAULT '',
    known_quests TEXT NOT NULL DEFAULT '[]',     -- quest ids already seen as turned in
    known_spells TEXT NOT NULL DEFAULT '[]'      -- borrowed capstone spells already seen
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, guid INTEGER, kind TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS quests (
    quest INTEGER PRIMARY KEY, guid INTEGER, title TEXT, target TEXT, spec TEXT, announcement TEXT,
    dm_note TEXT, story_beat TEXT, model TEXT,
    issued_at INTEGER, accepted_at INTEGER, completed_at INTEGER, retired_at INTEGER,
    status TEXT NOT NULL DEFAULT 'offered'       -- offered, accepted, completed, ignored, purged
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, guid INTEGER, name TEXT, spec TEXT,
    target TEXT, announcement TEXT, dm_note TEXT, story_beat TEXT, story_so_far TEXT,
    model TEXT, tokens_in INTEGER, tokens_out INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending, approved, rejected
    decided_at INTEGER, reason TEXT
);
CREATE TABLE IF NOT EXISTS seen_letters (id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guid INTEGER,
    status TEXT NOT NULL DEFAULT 'active',       -- active, completed, superseded
    premise TEXT, lure TEXT, adversary TEXT,
    beats TEXT,                                  -- JSON list of {level_band, intent}
    current_beat INTEGER NOT NULL DEFAULT 0,     -- index into beats
    signature_reward TEXT, seed TEXT, model TEXT, created_at INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS companions (
    quest INTEGER, guid INTEGER, name TEXT, descr TEXT,
    how TEXT,                                    -- party, shared (also took the bounty), nearby
    first_ts INTEGER, last_ts INTEGER, sightings INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (quest, guid, how)
);
"""

# Columns added after the first release; applied to an existing state.db on open.
ADDED_COLUMNS = (
    ("characters", "party", "TEXT NOT NULL DEFAULT '[]'"),     # who they were last seen grouped with
    ("quests", "completed_by", "TEXT"),
    ("quests", "circumstances", "TEXT"),
    ("proposals", "type", "TEXT NOT NULL DEFAULT 'bounty'"),   # bounty, arc (letter and gift to come)
    ("proposals", "payload", "TEXT"),                          # JSON for whatever the type needs
    ("quests", "giver", "TEXT"),                               # name of the NPC the bounty was offered through
    ("quests", "concludes_arc", "INTEGER"),                    # arc id this bounty is the finale of, if any
    ("arcs", "outcome", "TEXT"),                               # how it ended, written when the next arc is planned
    ("arcs", "ended_at", "INTEGER"),
    ("arcs", "end_reason", "TEXT"),                            # resolved, outgrown, replaced
)


def now():
    return int(time.time())


def connect():
    path = os.environ.get("DM_STATE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.db")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    for table, column, declaration in ADDED_COLUMNS:
        if column not in [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    db.commit()
    return db


def ago(ts, reference=None):
    """'25 minutes', '3 hours', '2 days' between a timestamp and now."""
    if not ts:
        return "unknown"
    seconds = max(0, (reference or now()) - ts)
    if seconds < 90:
        return "a minute"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    if seconds < 172800:
        return f"{round(seconds / 3600)} hours"
    return f"{round(seconds / 86400)} days"


def add_event(db, guid, kind, text, ts=None):
    db.execute("INSERT INTO events (ts, guid, kind, text) VALUES (?, ?, ?, ?)", (ts or now(), guid, kind, text))


def get_character(db, guid):
    return db.execute("SELECT * FROM characters WHERE guid = ?", (guid,)).fetchone()


def save_character(db, who, known_quests, known_spells, party, first=False):
    stamp = now()
    if first:
        db.execute("INSERT INTO characters (guid, name, race, class, level, zone, first_seen, last_seen, "
                   "known_quests, known_spells, party) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (who["guid"], who["name"], who["race"], who["class"], who["level"], who["zone"], stamp, stamp,
                    json.dumps(sorted(known_quests)), json.dumps(sorted(known_spells)), json.dumps(party)))
    else:
        db.execute("UPDATE characters SET name = ?, level = ?, zone = ?, last_seen = ?, known_quests = ?, "
                   "known_spells = ?, party = ? WHERE guid = ?",
                   (who["name"], who["level"], who["zone"], stamp, json.dumps(sorted(known_quests)),
                    json.dumps(sorted(known_spells)), json.dumps(party), who["guid"]))


def note_companion(db, quest, other, descr, how):
    """Record that `other` was seen with the bounty's character. Repeat sightings add up."""
    stamp = now()
    db.execute("INSERT INTO companions (quest, guid, name, descr, how, first_ts, last_ts) VALUES (?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT (quest, guid, how) DO UPDATE SET last_ts = excluded.last_ts, sightings = sightings + 1, "
               "descr = excluded.descr",
               (quest, other["guid"], other["name"], descr, how, stamp, stamp))


def companions_of(db, quest):
    return db.execute("SELECT * FROM companions WHERE quest = ? ORDER BY how, name", (quest,)).fetchall()


def open_quest(db, guid):
    """The bounty currently out for this character, if any."""
    return db.execute("SELECT * FROM quests WHERE guid = ? AND status IN ('offered', 'accepted') "
                      "ORDER BY issued_at DESC LIMIT 1", (guid,)).fetchone()


def last_quest(db, guid):
    return db.execute("SELECT * FROM quests WHERE guid = ? ORDER BY issued_at DESC LIMIT 1", (guid,)).fetchone()


def quest_history(db, guid, limit=6):
    found = db.execute("SELECT * FROM quests WHERE guid = ? ORDER BY issued_at DESC LIMIT ?", (guid, limit)).fetchall()
    return list(reversed(found))


def events_since(db, guid, ts, limit=25):
    return db.execute("SELECT * FROM events WHERE guid = ? AND ts > ? ORDER BY ts, id LIMIT ?",
                      (guid, ts, limit)).fetchall()


def pending_proposal(db, guid, kind=None):
    """A proposal for this character still waiting for a decision; optionally of one type."""
    if kind:
        return db.execute("SELECT * FROM proposals WHERE guid = ? AND status = 'pending' AND type = ?",
                          (guid, kind)).fetchone()
    return db.execute("SELECT * FROM proposals WHERE guid = ? AND status = 'pending'", (guid,)).fetchone()


def past_arcs(db, guid, limit=6):
    """Arcs that have ended for this character, oldest first."""
    found = db.execute("SELECT * FROM arcs WHERE guid = ? AND status <> 'active' ORDER BY id DESC LIMIT ?",
                       (guid, limit)).fetchall()
    return list(reversed(found))


def end_arc(db, arc, reason):
    """Close an arc. reason: resolved (its finale was turned in), outgrown, or replaced."""
    db.execute("UPDATE arcs SET status = ?, end_reason = ?, ended_at = ?, updated_at = ? WHERE id = ?",
               ("superseded" if reason == "replaced" else "completed", reason, now(), now(), arc["id"]))


def active_arc(db, guid):
    """The private plan the Overseer is following for this character, if one is approved."""
    return db.execute("SELECT * FROM arcs WHERE guid = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                      (guid,)).fetchone()


def proposals_in_last_hour(db):
    return db.execute("SELECT COUNT(*) FROM proposals WHERE ts > ?", (now() - 3600,)).fetchone()[0]
