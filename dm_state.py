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
CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, guid INTEGER, quest INTEGER,
    tier TEXT,                                   -- standard, prize, capstone
    items TEXT,                                  -- JSON list of item ids offered
    names TEXT,                                  -- JSON list of their names, for the story
    level INTEGER,                               -- the character's level when it was posted
    status TEXT NOT NULL DEFAULT 'posted'        -- posted, collected, lapsed
);
CREATE TABLE IF NOT EXISTS herald_voices (
    creature INTEGER PRIMARY KEY, name TEXT,
    note TEXT,                                   -- two sentences on how this NPC speaks
    pinned INTEGER NOT NULL DEFAULT 0,           -- 1 when the owner wrote or confirmed it
    set_at INTEGER, set_by_quest INTEGER, uses INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS canon (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
    guid INTEGER,                                -- the character it concerns, or NULL for the whole server
    zone INTEGER, creature INTEGER,              -- where and whom it concerns, when known
    fact TEXT,                                   -- one sentence, at most 25 words
    source TEXT,                                 -- 'bounty 30012', 'arc 7', 'chapter 3', 'owner'
    status TEXT NOT NULL DEFAULT 'active'        -- active, retired
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
    ("quests", "kind", "TEXT"),                                # hunt, mark, journey, party, trophy;
                                                               # a bounty's kind, not a proposal's type
    ("quests", "objectives", "TEXT"),                          # JSON: every objective, with names and labels
    ("quests", "giver_id", "INTEGER"),                         # creature id of the herald who offered it
    ("quests", "ender_id", "INTEGER"),                         # creature id of the NPC who receives the turn-in
    ("quests", "ender", "TEXT"),                               # and their name
    ("quests", "handed_over", "TEXT"),                         # what the character gives at turn-in, e.g. "12 Stolen Book"
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
    backfill_parties(db)
    db.commit()
    return db


def backfill_parties(db):
    """Fill who was party to each older bounty from what its record already holds.

    The spec has always carried the giver and ender as creature ids, and the
    objectives what a trophy asked for, so this is exact rather than a guess.
    """
    db.execute("UPDATE quests SET giver_id = json_extract(spec, '$.giver') "
               "WHERE giver_id IS NULL AND json_valid(spec)")
    db.execute("UPDATE quests SET ender_id = json_extract(spec, '$.ender'), "
               "ender = CASE WHEN json_extract(spec, '$.ender') = giver_id THEN giver END "
               "WHERE ender_id IS NULL AND json_valid(spec)")
    for row in db.execute("SELECT quest, objectives FROM quests WHERE handed_over IS NULL").fetchall():
        db.execute("UPDATE quests SET handed_over = ? WHERE quest = ?",
                   (handed_over(json.loads(row["objectives"] or "[]")), row["quest"]))


def handed_over(objectives):
    """What the character gives the NPC at turn-in: the props of a trophy bounty. Empty for kills."""
    return ", ".join(f"{o['count']} {o['prop_name']}" for o in objectives if o.get("prop_name"))


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


def record_reward(db, guid, quest, tier, items, names, level):
    db.execute("INSERT INTO rewards (ts, guid, quest, tier, items, names, level) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (now(), guid, quest, tier, json.dumps(items), json.dumps(names), level))


def set_reward_status(db, quest, status):
    """collected when the intended character turns the bounty in; lapsed when it goes unclaimed."""
    db.execute("UPDATE rewards SET status = ? WHERE quest = ? AND status = 'posted'", (status, quest))


def reward_of(db, quest):
    return db.execute("SELECT * FROM rewards WHERE quest = ? ORDER BY id DESC LIMIT 1", (quest,)).fetchone()


def gear_budget(db, guid, level, every, span):
    """The gear tiers this character's next bounty may pay: a tuple of 'standard' and 'prize'.

    every: at most one gear reward, of either tier, in this many consecutive
           bounties. A lapsed bounty still counts as a bounty, but not as gear.
    span:  levels the character must gain between prizes.
    """
    recent = [row["quest"] for row in db.execute(
        "SELECT quest FROM quests WHERE guid = ? ORDER BY issued_at DESC, quest DESC LIMIT ?",
        (guid, max(0, int(every) - 1)))]
    paid = {row["quest"] for row in db.execute(
        "SELECT quest FROM rewards WHERE guid = ? AND tier IN ('standard', 'prize') AND status <> 'lapsed'",
        (guid,))}
    if any(quest in paid for quest in recent):
        return ()
    last_prize = db.execute("SELECT MAX(level) FROM rewards WHERE guid = ? AND tier = 'prize' "
                            "AND status <> 'lapsed'", (guid,)).fetchone()[0]
    if last_prize is not None and level - last_prize < span:
        return ("standard",)
    return ("standard", "prize")


def herald_history(db, guid, creature, limit=2):
    """This character's latest bounties in which this NPC was the giver or received the turn-in, newest first."""
    return db.execute("SELECT * FROM quests WHERE guid = ? AND (giver_id = ? OR ender_id = ?) "
                      "ORDER BY issued_at DESC, quest DESC LIMIT ?", (guid, creature, creature, limit)).fetchall()


def voice_note(db, creature):
    return db.execute("SELECT * FROM herald_voices WHERE creature = ?", (creature,)).fetchone()


def all_voices(db):
    return db.execute("SELECT * FROM herald_voices ORDER BY name").fetchall()


def settle_voice(db, creature, name, note, quest=None):
    """Record a bounty through this NPC. The first note given is kept; later ones only count a use.

    Returns 'settled' when this note became theirs, 'kept' when one was already
    there, or None when there was nothing to record.
    """
    if voice_note(db, creature):
        db.execute("UPDATE herald_voices SET uses = uses + 1 WHERE creature = ?", (creature,))
        return "kept"
    note = " ".join(str(note or "").split())
    if not note:
        return None
    db.execute("INSERT INTO herald_voices (creature, name, note, set_at, set_by_quest, uses) VALUES (?, ?, ?, ?, ?, 1)",
               (creature, name, note, now(), quest))
    return "settled"


def set_voice(db, creature, name, note):
    """The owner writes or replaces a note; it is pinned."""
    db.execute("INSERT INTO herald_voices (creature, name, note, pinned, set_at) VALUES (?, ?, ?, 1, ?) "
               "ON CONFLICT (creature) DO UPDATE SET name = excluded.name, note = excluded.note, pinned = 1, "
               "set_at = excluded.set_at, set_by_quest = NULL",
               (creature, name, " ".join(str(note).split()), now()))


def forget_voice(db, creature):
    """Clear a note, so the next bounty through this NPC settles a new one."""
    return db.execute("DELETE FROM herald_voices WHERE creature = ?", (creature,)).rowcount


CANON_WORDS = 25        # a fact is one sentence of at most this many words
CANON_SHOWN = 10        # the most facts a prompt is shown


def clean_fact(text):
    """A fact in stored form, or '' when there is none. Overlong facts are refused, not cut."""
    text = " ".join(str(text or "").split())
    if not text or len(text.split()) > CANON_WORDS:
        return ""
    return text


def add_canon(db, guid, zone, creature, fact, source):
    """Record a fact the story has established. Returns its id, or None if it was empty, too long or already known."""
    fact = clean_fact(fact)
    if not fact:
        return None
    if db.execute("SELECT 1 FROM canon WHERE status = 'active' AND fact = ? COLLATE NOCASE", (fact,)).fetchone():
        return None
    db.execute("INSERT INTO canon (ts, guid, zone, creature, fact, source) VALUES (?, ?, ?, ?, ?, ?)",
               (now(), guid, zone, creature, fact, source))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def canon_for(db, guid, zone=None, creatures=(), limit=CANON_SHOWN):
    """Active facts a prompt about this character should respect, newest first.

    Its own facts, plus anyone's facts about the zone it is in or about an NPC
    on its herald list: that is how different characters' stories touch
    without contradicting each other. A fact tied to no character, zone or
    creature is true everywhere and always matches.
    """
    creatures = [int(c) for c in creatures if c is not None]
    marks = ",".join("?" * len(creatures)) or "NULL"
    return db.execute(f"SELECT * FROM canon WHERE status = 'active' AND (guid = ? OR zone = ? OR creature IN ({marks}) "
                      f"OR (guid IS NULL AND zone IS NULL AND creature IS NULL)) "
                      f"ORDER BY ts DESC, id DESC LIMIT ?", (guid, zone, *creatures, limit)).fetchall()


def all_canon(db, guid=None):
    """Every active fact, oldest first; with a guid, only that character's and the server-wide ones."""
    if guid is None:
        return db.execute("SELECT * FROM canon WHERE status = 'active' ORDER BY ts, id").fetchall()
    return db.execute("SELECT * FROM canon WHERE status = 'active' AND (guid = ? OR guid IS NULL) ORDER BY ts, id",
                      (guid,)).fetchall()


def retire_canon(db, fact_id):
    return db.execute("UPDATE canon SET status = 'retired' WHERE id = ? AND status = 'active'", (fact_id,)).rowcount


def proposals_in_last_hour(db):
    return db.execute("SELECT COUNT(*) FROM proposals WHERE ts > ?", (now() - 3600,)).fetchone()[0]
