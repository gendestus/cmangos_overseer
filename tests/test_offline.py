#!/usr/bin/env python3
"""Checks that need no game server, no database and no model.

    python3 -m unittest discover tests

They cover the rules the DM enforces on a model's answer, the SQL renderer,
the console allow-list and the memory file's schema upgrade. Anything that
talks to the game is tested by hand with the smoke tests in the README.
"""
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")

import apply_quest      # noqa: E402
import console          # noqa: E402
import db_users         # noqa: E402
import dm               # noqa: E402
import dm_state         # noqa: E402
import factions         # noqa: E402
import hot_quest        # noqa: E402
import world_query      # noqa: E402
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
    "givers": [{"creature": 197, "name": "Marshal McBride", "title": "", "distance": 32, "direction": "east"}],
    "party": [],
    "targets": [{"creature": 257, "name": "Kobold Worker", "rank": "normal", "min_level": 3, "max_level": 3,
                 "spawned": 21, "alive": 9, "distance": 300, "direction": "north", "tier": "near",
                 "unique": False, "needs_party": False},
                {"creature": 471, "name": "Narg the Taskmaster", "rank": "rare", "min_level": 4, "max_level": 4,
                 "spawned": 1, "alive": 1, "distance": 900, "direction": "south-west", "tier": "far",
                 "unique": True, "needs_party": False}],
    "reward_items": [{"item": 111520, "name": "Grimoire of Summon Voidwalker", "required_level": 10}],
    "money_cap": 650, "xp_weight": 100,
}

ANSWER = {
    "title": "Teeth in the Dark", "briefing": "Go, $N.", "objectives_text": "Slay 8 Kobold Workers.",
    "progress_text": "Not yet.", "completion_text": "Done.", "giver": 197, "target_creature": 257, "kill_count": 8,
    "reward_money_copper": 400, "reward_item": None, "announcement": "A bounty is posted.", "dm_note": "fits",
}

ARC = {
    "premise": "A paladin reaching past the Light.", "lure": "Make a knight who wields death.",
    "adversary": "the Scourge",
    "beats": [{"level_band": "3-5", "intent": "Test obedience."}, {"level_band": "5-8", "intent": "Turn them."},
              {"level_band": "8-11", "intent": "Pay it off."}],
    "signature_reward": "grimoire of death coil iii", "previous_outcome": "", "dm_note": "obvious candidate",
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
        self.assertEqual(spec["giver"], 197)                     # the herald the model picked
        self.assertEqual(spec["ender"], 197)
        self.assertEqual(target["name"], "Kobold Worker")
        self.assertEqual(target["giver"]["name"], "Marshal McBride")

    def test_herald_must_be_on_the_list(self):
        with self.assertRaises(write_quest.Rejected):
            self.build(giver=4991)

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

    def test_mini_arc_needs_two_to_four_beats(self):
        dm.validate_arc(dict(ARC, beats=ARC["beats"][:2]), self.BOOKS)
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(dict(ARC, beats=ARC["beats"][:1]), self.BOOKS)
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(dict(ARC, beats=ARC["beats"] + ARC["beats"]), self.BOOKS)

    def test_mini_arc_stays_within_reach_of_the_character(self):
        dm.validate_arc(ARC, self.BOOKS, level=3)                # bands end at 11, within ten levels of 3
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(ARC, self.BOOKS, level=0)            # same bands, more than ten levels ahead

    def test_arc_is_outgrown_past_its_last_band(self):
        arc = {"beats": json.dumps(ARC["beats"])}
        self.assertFalse(dm.arc_outgrown(arc, 12))
        self.assertTrue(dm.arc_outgrown(arc, 13))

    def test_signature_reward_must_be_on_the_list_or_empty(self):
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(ARC, ["Tome of Blink"])
        self.assertEqual(dm.validate_arc(dict(ARC, signature_reward=""), [])["signature_reward"], "")

    def test_beat_needs_a_level_band(self):
        beats = [dict(ARC["beats"][0], level_band="early")] + ARC["beats"][1:]
        with self.assertRaises(write_quest.Rejected):
            dm.validate_arc(dict(ARC, beats=beats), self.BOOKS)


class FriendOrFoe(unittest.TestCase):
    """The faction logic, against a tiny hand-built pair of data files."""

    @classmethod
    def setUpClass(cls):
        folder = tempfile.mkdtemp()

        def write(name, fields, rows):
            body = b"".join(struct.pack(f"<{fields}I", *row) for row in rows)
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(b"WDBC" + struct.pack("<4I", len(rows), fields, fields * 4, 1) + body + b"\0")

        # id, faction, flags, our mask, friend mask, enemy mask, 4 enemy factions, 4 friend factions
        write("FactionTemplate.dbc", 14, [
            (1, 1, 0, 3, 2, 4, 0, 0, 0, 0, 0, 0, 0, 0),       # Human player
            (2, 2, 0, 5, 4, 2, 0, 0, 0, 0, 0, 0, 0, 0),       # Orc player
            (12, 72, 0, 2, 2, 4, 0, 0, 0, 0, 0, 0, 0, 0),     # Stormwind townsfolk
            (14, 14, 0, 8, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0),     # monster: enemy of all players
            (35, 35, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0, 0, 0),     # friendly to everyone
            (120, 21, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),    # neutral town: neither friend nor foe
            (900, 900, 0, 2, 2, 4, 1, 0, 0, 0, 0, 0, 0, 0),   # looks Alliance but lists Humans as enemies
        ])
        write("ChrRaces.dbc", 3, [(1, 0, 1), (2, 0, 2)])
        cls.tables = factions.Factions.load(folder)

    def test_city_faction_is_friend_to_one_side_and_foe_to_the_other(self):
        self.assertTrue(self.tables.is_friendly(12, 1))
        self.assertFalse(self.tables.is_hostile(12, 1))
        self.assertTrue(self.tables.is_hostile(12, 2))

    def test_monsters_are_hostile_and_friendly_to_all_is_friendly(self):
        self.assertTrue(self.tables.is_hostile(14, 1) and self.tables.is_hostile(14, 2))
        self.assertTrue(self.tables.is_friendly(35, 1) and self.tables.is_friendly(35, 2))

    def test_neutral_is_neither(self):
        self.assertFalse(self.tables.is_hostile(120, 1))
        self.assertFalse(self.tables.is_friendly(120, 1))

    def test_an_explicit_enemy_entry_beats_the_masks(self):
        self.assertTrue(self.tables.is_hostile(900, 1))
        self.assertFalse(self.tables.is_friendly(900, 1))

    def test_unknown_template_is_unknown(self):
        self.assertIsNone(self.tables.is_hostile(424242, 1))

    def test_compass_bearing(self):
        here = {"x": 0.0, "y": 0.0}                              # +x is north, +y is west
        self.assertEqual(world_query.bearing(here, 100, 0), "north")
        self.assertEqual(world_query.bearing(here, 0, -100), "east")
        self.assertEqual(world_query.bearing(here, -100, 100), "south-west")


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


class TargetSearch(unittest.TestCase):
    """What the model is allowed to be shown. The query itself needs a server."""

    def rows(self, **overrides):
        row = {"rank": "normal", "tier": "near", "unique": False, "needs_party": False}
        row.update(overrides)
        return row

    def test_the_far_tier_cannot_crowd_out_what_is_near(self):
        near = [self.rows(tier="near", creature=i) for i in range(20)]
        distant = [self.rows(tier="far", creature=100 + i) for i in range(20)]
        kept = world_query.trim(near + distant, 12)
        self.assertEqual(len(kept), 12)
        self.assertEqual(len([r for r in kept if r["tier"] == "near"]), 8)
        self.assertEqual(len([r for r in kept if r["tier"] == "far"]), 4)

    def test_the_far_tier_fills_the_list_when_little_is_near(self):
        near = [self.rows(tier="near", creature=1)]
        distant = [self.rows(tier="far", creature=100 + i) for i in range(20)]
        kept = world_query.trim(near + distant, 12)
        self.assertEqual(len(kept), 12)
        self.assertEqual(kept[0]["creature"], 1)         # the near one is still first

    def test_a_short_list_is_left_alone(self):
        self.assertEqual(len(world_query.trim([self.rows(tier="near", creature=1)], 12)), 1)
        self.assertEqual(world_query.trim([], 12), [])

    def test_every_rank_has_a_window_and_a_threshold(self):
        for name in world_query.RANK_NAMES.values():
            self.assertIn(name, world_query.RANK_WINDOW)
            self.assertIn(name, world_query.RANK_NEEDS_ALIVE)
        self.assertNotIn(3, world_query.RANK_NAMES)      # a world boss is never offered

    def test_a_hunt_needs_a_crowd_and_a_named_creature_does_not(self):
        self.assertGreater(world_query.RANK_NEEDS_ALIVE["normal"], 1)
        for named in ("rare", "elite", "rare elite"):
            self.assertEqual(world_query.RANK_NEEDS_ALIVE[named], 1)

    def test_the_party_note_reads_naturally(self):
        who = {"name": "Zachadin"}
        self.assertEqual(world_query.party_note(who, []), "Zachadin is alone.")
        self.assertEqual(world_query.party_note(who, [{"name": "Bo", "level": 9, "online": 0}]),
                         "Zachadin is alone.")           # offline company is no help
        self.assertEqual(world_query.party_note(who, [{"name": "Bo", "level": 9, "online": 1}]),
                         "Zachadin is in a party of 2, with Bo, level 9.")


class WhatTheModelIsTold(unittest.TestCase):
    def message(self, context=None):
        return write_quest.user_message(context or CONTEXT, None)

    def test_a_target_carries_its_rank_bearing_and_count(self):
        line = [l for l in self.message().splitlines() if "Kobold Worker" in l][0]
        self.assertIn("normal", line)
        self.assertIn("9 alive", line)
        self.assertIn("300 yards north", line)

    def test_a_far_target_is_called_a_journey(self):
        line = [l for l in self.message().splitlines() if "Narg" in l][0]
        self.assertIn("a journey", line)
        self.assertIn("the only one", line)              # a single spawn is not a crowd
        self.assertIn("rare", line)

    def test_an_elite_says_it_needs_the_party(self):
        context = dict(CONTEXT, targets=[dict(CONTEXT["targets"][0], name="Hogger", rank="elite",
                                              needs_party=True)])
        self.assertIn("needs the party", self.message(context))

    def test_the_party_is_stated(self):
        self.assertIn("Zachadin is alone.", self.message())
        with_party = dict(CONTEXT, party=[{"name": "Bo", "level": 4, "online": 1}])
        self.assertIn("party of 2", self.message(with_party))

    def test_a_named_target_caps_the_kill_count_at_one(self):
        answer = dict(ANSWER, target_creature=471, kill_count=3)
        with self.assertRaises(write_quest.Rejected):     # only one is alive
            write_quest.build_spec(answer, CONTEXT, 30000)
        spec, _, _ = write_quest.build_spec(dict(answer, kill_count=1), CONTEXT, 30000)
        self.assertEqual(spec["kill"], [{"creature": 471, "count": 1}])


class KillSwitch(unittest.TestCase):
    """One flag stops everything that reaches the game; reading is unaffected."""

    def setUp(self):
        self.flag = os.path.join(tempfile.mkdtemp(), "paused")
        os.environ["DM_PAUSE_FILE"] = self.flag
        os.environ.pop("DM_PAUSE", None)

    def tearDown(self):
        os.environ.pop("DM_PAUSE_FILE", None)
        os.environ.pop("DM_PAUSE", None)

    def write_flag(self, reason):
        with open(self.flag, "w", encoding="utf-8") as handle:
            handle.write(reason)

    def test_not_paused_by_default(self):
        self.assertIsNone(console.paused())

    def test_the_flag_file_carries_its_reason(self):
        self.write_flag("a player complained")
        self.assertEqual(console.paused(), "a player complained")

    def test_an_empty_flag_file_still_pauses(self):
        self.write_flag("   \n")
        self.assertEqual(console.paused(), "paused, with no reason given")

    def test_the_environment_pauses_too(self):
        os.environ["DM_PAUSE"] = "1"
        self.assertEqual(console.paused(), "DM_PAUSE is set")

    def test_no_console_command_is_sent_while_paused(self):
        self.write_flag("testing")
        with self.assertRaises(console.Paused):
            console.run("server info")

    def test_nothing_is_written_while_paused(self):
        self.write_flag("testing")
        with self.assertRaises(apply_quest.StepFailed):
            apply_quest.run_sql("SELECT 1;")

    def test_an_allow_list_refusal_still_comes_first(self):
        self.write_flag("testing")
        with self.assertRaises(console.NotAllowed):          # not Paused: the command was never allowed
            console.run("account delete Bob")


class Purge(unittest.TestCase):
    def test_removal_needs_only_an_id(self):
        sql = hot_quest.render_remove_by_id(30001)
        self.assertIn("DELETE FROM quest_template WHERE entry = 30001", sql)
        self.assertIn("character_queststatus WHERE quest = 30001", sql)

    def test_a_title_the_authoring_rules_would_refuse_is_still_removable(self):
        sql = hot_quest.render_remove_by_id(30001, "x" * 200)       # validate() caps a title at 80
        self.assertIn("DELETE FROM quest_template WHERE entry = 30001", sql)

    def test_a_title_cannot_break_out_of_its_comment(self):
        sql = hot_quest.render_remove_by_id(30001, "oops\nDELETE FROM creature;")
        lines = sql.splitlines()
        self.assertIn("oops DELETE FROM creature;", lines[0])    # folded onto the comment, where it is inert
        for line in lines[1:]:                                   # and nowhere a statement could run
            self.assertNotIn("creature;", line)

    def test_only_the_dm_range_can_be_purged(self):
        for quest_id in (1, 29999, 40000):
            with self.assertRaises(hot_quest.SpecError):
                hot_quest.render_remove_by_id(quest_id)


class NarrowDatabaseUsers(unittest.TestCase):
    """The writer's grants must cover every table the renderer writes, and no more.

    This is the test that fails when a later phase writes to a new table and
    nobody widens the grant; the symptom in play would be a refused query.
    """

    def granted(self, grants):
        return {target.split(".", 1)[-1] for _, target in grants}

    def written_tables(self):
        quest = hot_quest.validate(SPEC)
        sql = hot_quest.render_apply(quest) + hot_quest.render_remove(quest) + hot_quest.render_retire(quest)
        found = set()
        for statement in ("REPLACE INTO", "INSERT INTO", "DELETE FROM", "UPDATE"):
            for match in re.finditer(statement + r" ([a-zA-Z_][\w.]*)", sql):
                found.add(match.group(1).split(".", 1)[-1])
        return found

    def test_every_table_written_is_granted(self):
        missing = self.written_tables() - self.granted(db_users.WRITE_GRANTS)
        self.assertEqual(missing, set(), f"the writer has no grant for: {sorted(missing)}")

    def test_the_writer_cannot_touch_anything_it_does_not_write(self):
        for table in ("creature", "creature_template", "item_template", "mail", "characters"):
            for privilege, target in db_users.WRITE_GRANTS:
                if target.endswith("." + table):
                    self.assertNotIn("INSERT", privilege, f"{table} should be read-only to the writer")
                    if table != "character_queststatus":
                        self.assertNotIn("DELETE", privilege, f"{table} should be read-only to the writer")

    def test_the_reader_is_granted_nothing_but_select(self):
        for privilege, _ in db_users.READ_GRANTS:
            self.assertEqual(privilege, "SELECT")


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

    def test_a_target_is_not_reused_from_the_last_bounties(self):
        os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")
        db = dm_state.connect()
        self.assertEqual(dm.recent_targets(db, 1), [])
        for quest, creature, at in ((30000, 69, 1), (30001, 257, 2), (30002, 38, 3)):
            db.execute("INSERT INTO quests (quest, guid, title, target, spec, issued_at, status) "
                       "VALUES (?, 1, 'T', 'name only', ?, ?, 'completed')",
                       (quest, json.dumps({"kill": [{"creature": creature, "count": 4}]}), at))
        # The creature comes from the spec; the target column holds only a name.
        self.assertEqual(sorted(dm.recent_targets(db, 1)), [38, 257])
        self.assertEqual(sorted(dm.recent_targets(db, 1, count=3)), [38, 69, 257])
        db.close()

    def test_a_bounty_with_an_unreadable_spec_is_skipped_not_fatal(self):
        os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")
        db = dm_state.connect()
        db.execute("INSERT INTO quests (quest, guid, title, spec, issued_at, status) "
                   "VALUES (30000, 1, 'T', 'not json', 1, 'completed')")
        self.assertEqual(dm.recent_targets(db, 1), [])
        db.close()

    def test_every_bounty_status_can_be_described(self):
        """story_section builds the model's prompt; an unknown status must not stop it."""
        os.environ["DM_STATE"] = os.path.join(tempfile.mkdtemp(), "state.db")
        db = dm_state.connect()
        who = {"guid": 1, "name": "Zachadin", "race": 1, "class": 2, "level": 4, "zone": 12}
        dm_state.save_character(db, who, [], [], [], first=True)
        for i, status in enumerate(("completed", "accepted", "offered", "ignored", "purged", "something new")):
            db.execute("INSERT INTO quests (quest, guid, title, target, spec, issued_at, completed_at, "
                       "story_beat, status) VALUES (?, 1, 'T', 'Wolf', ?, 1, 2, 'b', ?)",
                       (30000 + i, json.dumps({"kill": [{"creature": 69, "count": 4}]}), status))
        text = dm.story_section(db, who)                 # must not raise
        self.assertIn("withdrawn by the server owner", text)
        self.assertIn("something new", text)
        db.close()

    def test_elapsed_time_reads_naturally(self):
        self.assertEqual(dm_state.ago(1000, 1030), "a minute")
        self.assertEqual(dm_state.ago(1000, 1000 + 25 * 60), "25 minutes")
        self.assertEqual(dm_state.ago(1000, 1000 + 3 * 3600), "3 hours")


if __name__ == "__main__":
    unittest.main()
