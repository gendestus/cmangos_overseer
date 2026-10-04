#!/usr/bin/env python3
"""Render a dungeon-master quest spec (JSON) into SQL for CMaNGOS Classic.

The SQL is applied to the live world database, then the server is told to
re-read its quests with `.reload all_quest`. No restart, no client patch.

    python3 hot_quest.py test_quest.json            > apply.sql
    python3 hot_quest.py test_quest.json --remove   > remove.sql

This file only renders text. It never connects to anything, so the service
that later wraps it decides how SQL reaches the database and how the reload
command is sent.

Spec fields
    id               quest id, 30000-39999 (the range reserved for the DM)
    title            quest name
    zone             zone id the quest is filed under in the quest log
    min_level        lowest character level that is offered the quest
    quest_level      the quest's own level (drives colour and XP)
    giver, ender     creature ids; both must have the quest-giver flag
    briefing         text shown when the quest is offered
    objectives_text  the one-line summary under "Quest Objectives"
    progress_text    what the ender says while the quest is incomplete
    completion_text  what the ender says at turn-in
    kill             up to 4 of {creature, count}
    collect          up to 4 of {item, count}; kill + collect <= 4 each
    reward.money_copper   coins given (100 = 1 silver)
    reward.xp_weight      the stock table's XP input (RewMoneyMaxLevel); copy
                          it from a stock quest of the same level
    reward.items          up to 4 of {item, count}, all given
    reward.choice_items   up to 6 of {item, count}, player picks one
    reward.teach_spell    null, or {shown, cast}: `cast` is the teach spell
                          run at turn-in, `shown` is the spell displayed

In text, $N is the character's name, $C the class, $R the race. A newline
becomes the client's line-break code ($B).
"""
import argparse
import json
import os
import sys

QUEST_ID_RANGE = (30000, 39999)
QUEST_FLAGS_SHARABLE = 8
# Database names. cmangos-deploy uses these; override only for a test copy.
WORLD_DB = os.environ.get("DM_WORLD_DB", "mangos")
CHAR_DB = os.environ.get("DM_CHAR_DB", "characters")


class SpecError(ValueError):
    pass


def sql_text(value):
    """Quote a string for SQL and convert newlines to the client's $B."""
    text = str(value).replace("\r\n", "\n").replace("\n", "$B")
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def as_int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError(f"{name} must be a whole number")
    if not low <= value <= high:
        raise SpecError(f"{name} must be between {low} and {high}, got {value}")
    return value


def pairs(entries, id_key, name, limit):
    """Validate a list of {id_key, count} objects."""
    entries = entries or []
    if len(entries) > limit:
        raise SpecError(f"{name}: at most {limit} entries, got {len(entries)}")
    out = []
    for i, entry in enumerate(entries, start=1):
        ident = as_int(entry.get(id_key), f"{name}[{i}].{id_key}", 1, 16777215)
        count = as_int(entry.get("count"), f"{name}[{i}].count", 1, 65535)
        out.append((ident, count))
    return out


def validate(spec):
    q = {}
    q["id"] = as_int(spec.get("id"), "id", *QUEST_ID_RANGE)
    q["zone"] = as_int(spec.get("zone"), "zone", 1, 32767)
    q["min_level"] = as_int(spec.get("min_level"), "min_level", 1, 60)
    q["quest_level"] = as_int(spec.get("quest_level"), "quest_level", 1, 63)
    q["giver"] = as_int(spec.get("giver"), "giver", 1, 16777215)
    q["ender"] = as_int(spec.get("ender"), "ender", 1, 16777215)

    for field, limit in (("title", 80), ("briefing", 2000), ("objectives_text", 400),
                         ("progress_text", 1000), ("completion_text", 2000)):
        text = spec.get(field)
        if not isinstance(text, str) or not text.strip():
            raise SpecError(f"{field} is required")
        if len(text) > limit:
            raise SpecError(f"{field} is {len(text)} characters; the limit here is {limit}")
        q[field] = text

    q["kill"] = pairs(spec.get("kill"), "creature", "kill", 4)
    q["collect"] = pairs(spec.get("collect"), "item", "collect", 4)
    if not q["kill"] and not q["collect"]:
        raise SpecError("the quest needs at least one kill or collect objective")

    reward = spec.get("reward") or {}
    q["money"] = as_int(reward.get("money_copper", 0), "reward.money_copper", 0, 10_000_000)
    q["xp_weight"] = as_int(reward.get("xp_weight", 0), "reward.xp_weight", 0, 1_000_000)
    q["items"] = pairs(reward.get("items"), "item", "reward.items", 4)
    q["choice"] = pairs(reward.get("choice_items"), "item", "reward.choice_items", 6)
    teach = reward.get("teach_spell")
    if teach:
        q["rew_spell"] = as_int(teach.get("shown"), "reward.teach_spell.shown", 1, 16777215)
        q["rew_spell_cast"] = as_int(teach.get("cast"), "reward.teach_spell.cast", 1, 16777215)
    else:
        q["rew_spell"] = q["rew_spell_cast"] = 0
    return q


def preflight(q):
    """One SELECT that reports whether everything the quest points at exists."""
    checks = [
        (f"giver {q['giver']} exists and gives quests",
         f"EXISTS(SELECT 1 FROM creature_template WHERE Entry = {q['giver']} AND (NpcFlags & 2) <> 0)"),
        (f"giver {q['giver']} is spawned",
         f"EXISTS(SELECT 1 FROM creature WHERE id = {q['giver']})"),
        (f"ender {q['ender']} exists and gives quests",
         f"EXISTS(SELECT 1 FROM creature_template WHERE Entry = {q['ender']} AND (NpcFlags & 2) <> 0)"),
    ]
    for creature, _ in q["kill"]:
        checks.append((f"kill target {creature} exists",
                       f"EXISTS(SELECT 1 FROM creature_template WHERE Entry = {creature})"))
        checks.append((f"kill target {creature} is spawned",
                       f"EXISTS(SELECT 1 FROM creature WHERE id = {creature})"))
    for item, _ in q["collect"] + q["items"] + q["choice"]:
        checks.append((f"item {item} exists",
                       f"EXISTS(SELECT 1 FROM item_template WHERE entry = {item})"))
    for spell in filter(None, (q["rew_spell"], q["rew_spell_cast"])):
        checks.append((f"spell {spell} exists",
                       f"EXISTS(SELECT 1 FROM spell_template WHERE Id = {spell})"))
    rows = [f"SELECT {sql_text(label)} AS preflight, IF({test}, 'ok', 'PROBLEM') AS result"
            for label, test in checks]
    return "\nUNION ALL\n".join(rows) + ";"


def render_preflight(q):
    """The preflight on its own, so a caller can check before writing."""
    return f"USE {WORLD_DB};\n\n{preflight(q)}\n"


def padded(values, size):
    return values + [(0, 0)] * (size - len(values))


def render_apply(q):
    columns = {
        "entry": q["id"],
        "Method": 2,                       # normal quest: accept, do, turn in
        "ZoneOrSort": q["zone"],
        "MinLevel": q["min_level"],
        "QuestLevel": q["quest_level"],
        "QuestFlags": QUEST_FLAGS_SHARABLE,
        "Title": sql_text(q["title"]),
        "Details": sql_text(q["briefing"]),
        "Objectives": sql_text(q["objectives_text"]),
        "RequestItemsText": sql_text(q["progress_text"]),
        "OfferRewardText": sql_text(q["completion_text"]),
        "RewOrReqMoney": q["money"],
        "RewMoneyMaxLevel": q["xp_weight"],
        "RewSpell": q["rew_spell"],
        "RewSpellCast": q["rew_spell_cast"],
    }
    for i, (ident, count) in enumerate(padded(q["kill"], 4), start=1):
        columns[f"ReqCreatureOrGOId{i}"] = ident
        columns[f"ReqCreatureOrGOCount{i}"] = count
    for i, (ident, count) in enumerate(padded(q["collect"], 4), start=1):
        columns[f"ReqItemId{i}"] = ident
        columns[f"ReqItemCount{i}"] = count
    for i, (ident, count) in enumerate(padded(q["items"], 4), start=1):
        columns[f"RewItemId{i}"] = ident
        columns[f"RewItemCount{i}"] = count
    for i, (ident, count) in enumerate(padded(q["choice"], 6), start=1):
        columns[f"RewChoiceItemId{i}"] = ident
        columns[f"RewChoiceItemCount{i}"] = count

    names = ",\n  ".join(columns)
    values = ",\n  ".join(str(v) for v in columns.values())
    qid = q["id"]
    return f"""-- Hot-loaded DM quest {qid}: {q['title']}
-- Apply to the live world database, then run in game or on the console:
--     .reload all_quest
-- Safe to apply again: the quest row and its giver/ender links are replaced.

USE {WORLD_DB};

-- 1. Preflight: every row should say ok. A PROBLEM row means the quest points
--    at something that is missing; the server will log it and the quest may
--    not be completable.
{preflight(q)}

-- 2. The quest. Columns not listed take their table defaults, so a re-apply
--    always produces the same row.
REPLACE INTO quest_template (
  {names}
) VALUES (
  {values}
);

-- 3. Who offers it and who takes the turn-in.
DELETE FROM creature_questrelation WHERE quest = {qid};
INSERT INTO creature_questrelation (id, quest) VALUES ({q['giver']}, {qid});

DELETE FROM creature_involvedrelation WHERE quest = {qid};
INSERT INTO creature_involvedrelation (id, quest) VALUES ({q['ender']}, {qid});
"""


def render_remove_by_id(quest_id, label=""):
    """Removal SQL for one quest id. Needs nothing but the id, so it can clean
    up a quest whose spec is lost or whose text no longer passes validate()."""
    qid = as_int(quest_id, "id", *QUEST_ID_RANGE)
    label = " ".join(str(label or "").split())      # a title from the database stays on the comment line
    return f"""-- Remove DM quest {qid}{f": {label}" if label else ""}
-- Apply, run `.reload all_quest`, and have anyone holding the quest relog.

USE {WORLD_DB};

DELETE FROM creature_questrelation WHERE quest = {qid};
DELETE FROM creature_involvedrelation WHERE quest = {qid};
DELETE FROM quest_template WHERE entry = {qid};

-- Forget every character's progress and completion of it.
DELETE FROM {CHAR_DB}.character_queststatus WHERE quest = {qid};
"""


def render_remove(q):
    return render_remove_by_id(q["id"], q["title"])


def render_retire(q):
    """Stop offering the quest but keep it: anyone holding it can still turn it
    in, and completion history is kept."""
    qid = q["id"]
    return f"""-- Retire DM quest {qid}: {q['title']} (no longer offered; history kept)

USE {WORLD_DB};

DELETE FROM creature_questrelation WHERE quest = {qid};
"""


def main():
    parser = argparse.ArgumentParser(description="Render a DM quest spec into SQL.")
    parser.add_argument("spec", help="path to the quest spec JSON")
    parser.add_argument("--remove", action="store_true", help="render the removal SQL instead")
    args = parser.parse_args()
    try:
        with open(args.spec, encoding="utf-8") as handle:
            quest = validate(json.load(handle))
    except (OSError, json.JSONDecodeError, SpecError) as error:
        sys.exit(f"hot_quest: {error}")
    sys.stdout.write(render_remove(quest) if args.remove else render_apply(quest))


if __name__ == "__main__":
    main()
