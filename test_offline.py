#!/usr/bin/env python3
"""Checks that need no game server, no database and no model.

    python3 -m unittest discover tests

They cover the rules the DM enforces on a model's answer, the SQL renderer,
the console allow-list and the memory file's schema upgrade. Anything that
talks to the game is tested by hand with the smoke tests in the README.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")

import console          # noqa: E402
import dm               # noqa: E402
import dm_state         # noqa: E402
import hot_quest        # noqa: E402
import write_quest      # noqa: E402

SPEC = {
    "id": 30000, "title": "Test", "zone": 12, "min_level": 1, "quest_level": 2, "giver": 4991, "ender": 4991,
    "briefing": "Line one, $N.\nLine two.", "objectives_text": "Kill 4.", "progress_text": "Not yet.",
    "completion_text": "It's done.", "kill": [{"creature": 6, "count": 4}], "collect": [],
    "reward": {"money_copper": 500, "xp_weight": 100, "items": [{"item": 111520, "count": 1}],
               "choice_items": [], "teach_spell": None},
}

CONTEXT = {
    "character": {"guid": 1, "name": "Zachadin", "level": 3, "zone": 12, "class": 2, "race": 1,
                  "race_name": "Human", "class_name": "Paladin"},
    "giver": {"creature": 4991, "name": "World Shaman Trainer", "distance": 20},
    "targets": [{"creature": 257, "name": "Kobold Worker", "min_level": 3, "max_level": 3,
                 "spawned": 21, "alive": 9, "distance": 300}],
    "reward_items": [{"item": 111520, "name": "Grimoire of Summon Voidwalker", "required_level": 10}],
    "money_cap": 650, "xp_weight": 100,
}

ANSWER = {
    "title": "Teeth in the Dark", "briefing": "Go, $N.", "objectives_text": "Slay 8 Kobold Workers.",
    "progress_text": "Not yet.", "completion_text": "Done.", "target_creature": 257, "kill_count": 8,
    "reward_money_copper": 400, "reward_item": None, "announcement": "A bounty is posted.", "dm_note": "fits",
}

ARC = {
    "premise": "A paladin reaching past the Light.", "lure": "Make a knight who wields death.",
    "adversary": "the Scourge",
    "beats": [{"level_band": "3-10", "intent": "Test obedience."}, {"level_band": "10-20", "intent": "Turn them."},
              {"level_band": "20-40", "intent": "Pay it off."}],
    "signature_reward": "grimoire of death coil iii", "dm_note": "obvious candidate",
}


class QuestSpec(unittest.TestCase):
    def test_valid_spec_renders(self):
        sql = hot_quest.render_apply(hot_quest.validate(SPEC))
        self.assertIn("REPLACE INTO quest_template", sql)
        self.assertIn("Line one, $N.$BLine two.", sql)           # newline becomes the client's break code
        self.assertIn("'It''s done.'", sql)                      # quotes are escaped
        self.assertIn("INSERT INTO creature_questrelation (id, quest) VALUES (4991, 30000)", sql)

    def test_id_outside_dm_range_is_refused(self):
        with self.assertRaises(hot_quest.SpecError):
            hot_quest.validate(dict(SPEC, id=7))

    def test_quest_needs_an_objective(self):
        with self.assertRaises(hot_quest.SpecError):
            hot_quest.validate(dict(SPEC, kill=[], collect=[]))

    def test_retire_keeps_the_quest(self):
        sql = hot_quest.render_retire(hot_quest.validate(SPEC))
        self.assertIn("DELETE FROM creature_questrelation", sql)
        self.assertNotIn("DELETE FROM quest_template", sql)


class ModelAnswerRules(unittest.TestCase):
    def build(self, **changes):
        return write_quest.build_spec(dict(ANSWER, **changes), CONTEXT, 30000)

    def test_good_answer_becomes_a_spec(self):
        spec, target, announcement = self.build()
        self.assertEqual(spec["kill"], [{"creature": 257, "count": 8}])
        self.assertEqual(spec["giver"], 4991)
        self.assertEqual(target["name"], "Kobold Worker")

    def test_target_must_be_on_the_list(self):
        with self.assertRaises(write_quest.Rejected):
            self.build(target_creature=1642)

    def test_kills_cannot_exceed_what_is_alive(self):
        with self.assertRaises(write_quest.Rejected):
            self.build(kill_count=10)

    def test_money_cannot_exceed_the_cap(self):
        with self.assertRaises(write_quest.Rejected):
            self.build(reward_money_copper=651)

    def test_reward_item_must_be_on_the_list(self):
        with self.assertRaises(write_quest.Rejected):
            self.build(reward_item=19019)
        spec, _, _ = self.build(reward_item=111520)
        self.assertEqual(spec["reward"]["items"], [{"item": 111520, "count": 1}])


class ArcRules(unittest.TestCase):
    BOOKS = ["Grimoire of Death Coil III", "Tome of Blink"]

    def test_good_arc_is_stored_with_the_listed_book_name(self):
        arc = dm.validate_arc(ARC, self.BOOKS)
        self.assertEqual(arc["signature_reward"], "Grimoire of Death Coil III")
        self.assertEqual(len(arc["beats"]), 3)

    def test_arc_needs_three_to_five_beats(self):
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(dict(ARC, beats=ARC["beats"][:2]), self.BOOKS)

    def test_signature_reward_must_be_on_the_list_or_empty(self):
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(ARC, ["Tome of Blink"])
        self.assertEqual(dm.validate_arc(dict(ARC, signature_reward=""), [])["signature_reward"], "")

    def test_beat_needs_a_level_band(self):
        beats = [dict(ARC["beats"][0], level_band="early")] + ARC["beats"][1:]
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(dict(ARC, beats=beats), self.BOOKS)


class ConsoleAllowList(unittest.TestCase):
    def test_allowed_commands_pass(self):
        for command in ("server info", "reload all_quest", "announce hello there", "send mail Bob \"s\" \"t\""):
            console.check_allowed(console.normalise(command))
        self.assertEqual(console.normalise(".reload all_quest"), "reload all_quest")

    def test_everything_else_is_refused(self):
        for command in ("account set gmlevel BOB 3", "server shutdown 0", "reload all", "announcement x",
                        "announce hi\naccount delete X"):
            with self.assertRaises(console.NotAllowed):
                console.check_allowed(console.normalise(command))


class MemoryFile(unittest.TestCase):
    def test_old_file_is_upgraded_on_open(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE characters (guid INTEGER PRIMARY KEY, name TEXT, race INTEGER, class INTEGER, level INTEGER,
                zone INTEGER, first_seen INTEGER, last_seen INTEGER, story_so_far TEXT NOT NULL DEFAULT '',
                known_quests TEXT NOT NULL DEFAULT '[]', known_spells TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE quests (quest INTEGER PRIMARY KEY, guid INTEGER, title TEXT, target TEXT, spec TEXT,
                announcement TEXT, dm_note TEXT, story_beat TEXT, model TEXT, issued_at INTEGER,
                accepted_at INTEGER, completed_at INTEGER, retired_at INTEGER, status TEXT NOT NULL DEFAULT 'offered');
            CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, guid INTEGER, name TEXT,
                spec TEXT, target TEXT, announcement TEXT, dm_note TEXT, story_beat TEXT, story_so_far TEXT,
                model TEXT, tokens_in INTEGER, tokens_out INTEGER, status TEXT NOT NULL DEFAULT 'pending',
                decided_at INTEGER, reason TEXT);
            INSERT INTO proposals (guid, name, spec) VALUES (1, 'Zachadin', '{}');
        """)
        old.commit()
        old.close()
        os.environ["DM_STATE"] = path
        db = dm_state.connect()
        columns = lambda table: [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
        self.assertIn("party", columns("characters"))
        self.assertIn("circumstances", columns("quests"))
        self.assertIn("type", columns("proposals"))
        self.assertEqual(db.execute("SELECT type FROM proposals").fetchone()[0], "bounty")   # old rows are bounties
        self.assertIn("arcs", [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")])
        db.close()

    def test_one_bounty_at_a_time(self):
        os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")
        db = dm_state.connect()
        who = {"guid": 1, "name": "Zachadin"}
        self.assertIsNone(dm.wants_bounty(db, who))
        db.execute("INSERT INTO quests (quest, guid, title, spec, issued_at, status) VALUES (30000, 1, 'T', '{}', 1, 'accepted')")
        self.assertEqual(dm.wants_bounty(db, who), "a bounty is already out")       # accepted: blocks, never expires
        db.execute("UPDATE quests SET status = 'completed', completed_at = 1")
        self.assertIsNone(dm.wants_bounty(db, who))                                 # finished long ago: free again
        db.close()

    def test_elapsed_time_reads_naturally(self):
        self.assertEqual(dm_state.ago(1000, 1030), "a minute")
        self.assertEqual(dm_state.ago(1000, 1000 + 25 * 60), "25 minutes")
        self.assertEqual(dm_state.ago(1000, 1000 + 3 * 3600), "3 hours")


if __name__ == "__main__":
    unittest.main()
