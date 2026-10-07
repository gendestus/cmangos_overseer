#!/usr/bin/env python3
"""Read-only questions the DM asks the game databases.

    python3 world_query.py Zachadin        # print everything known for a character

Nothing here writes. Every function returns plain dicts and lists, ready to be
shown to a model or a person.

Settings (environment or the .env beside this file):
    DM_COMPOSE_DIR        folder holding compose.yaml (default ~/cmangos-deploy)
    DM_DB_QUERY_COMMAND   command that reads SQL on stdin and prints raw rows;
                          default runs the mariadb client in the database container
    DM_DBC_DIR            folder of extracted .dbc files, for friend-or-foe checks (see factions.py)
    DM_PRIZE_REACH        how many levels above the character a prize may be (default 5)
"""
import json
import math
import os
import re
import shlex
import subprocess
import sys

import console
import factions
import hot_quest

DEFAULT_QUERY_COMMAND = (
    "docker compose exec -T database "
    "sh -c 'mariadb -N -B -r -u root -p\"$MARIADB_ROOT_PASSWORD\"'"
)

RACES = {1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf", 5: "Undead", 6: "Tauren", 7: "Gnome", 8: "Troll"}
CLASSES = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 7: "Shaman", 8: "Mage",
           9: "Warlock", 11: "Druid"}

# Creature types never offered as kill targets: critters, totems, vanity pets.
SKIP_CREATURE_TYPES = (8, 11, 12)
GUARD_FLAG = 1024
# Faction templates of the player cities and "friendly to all". Used only when
# the game's faction files cannot be read (see factions.py); with them, friend
# or foe is decided exactly, per race.
PLAYER_FACTIONS = (11, 12, 35, 53, 55, 57, 64, 79, 80, 84, 85, 29, 68, 71, 98, 104, 105, 118, 122, 126, 875, 876, 877)


class QueryError(Exception):
    pass


def rows(sql):
    """Run a SELECT whose single column is a JSON object; return the objects."""
    console.load_env()
    command = shlex.split(os.environ.get("DM_DB_QUERY_COMMAND", DEFAULT_QUERY_COMMAND))
    folder = os.path.expanduser(os.environ.get("DM_COMPOSE_DIR", "~/cmangos-deploy"))
    try:
        done = subprocess.run(command, input=sql, text=True, capture_output=True,
                              cwd=folder if os.path.isdir(folder) else None, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QueryError(f"could not run the database command: {error}") from None
    if done.returncode != 0:
        raise QueryError(f"database rejected the query:\n{done.stderr.strip()}")
    return [json.loads(line) for line in done.stdout.splitlines() if line.strip().startswith("{")]


def character(name):
    """One character by name, or None."""
    if not re.fullmatch(r"[A-Za-z]{2,12}", name or ""):
        raise QueryError("character names are 2 to 12 letters")
    found = rows(f"""
        SELECT JSON_OBJECT('guid', guid, 'name', name, 'race', race, 'class', class, 'level', level,
                           'map', map, 'zone', zone, 'x', ROUND(position_x, 1), 'y', ROUND(position_y, 1),
                           'online', online, 'money', money)
        FROM {hot_quest.CHAR_DB}.characters WHERE name = '{name}';""")
    if not found:
        return None
    who = found[0]
    who["race_name"] = RACES.get(who["race"], str(who["race"]))
    who["class_name"] = CLASSES.get(who["class"], str(who["class"]))
    return who


def bearing(who, x, y):
    """Compass direction from the character to a point. In this game +x is north and +y is west."""
    north, west = x - who["x"], y - who["y"]
    if abs(north) < 1 and abs(west) < 1:
        return "here"
    angle = math.degrees(math.atan2(-west, north)) % 360          # 0 = north, 90 = east
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[
        int((angle + 22.5) // 45) % 8]


# Creature ranks, from creature_template.Rank. Rank 3 is a world boss and is
# never offered. A rare is a named creature one player is meant to beat; an
# elite is not, unless the character has help or a wide level margin.
RANK_NAMES = {0: "normal", 1: "elite", 2: "rare elite", 4: "rare"}
NEAR_YARDS = 400
FAR_YARDS = 1500
# Level window per rank, as (levels below the character, levels above).
RANK_WINDOW = {"normal": (3, 2), "rare": (4, 2), "elite": (6, 1), "rare elite": (6, 1)}
# How many must be alive for the rank to be worth offering: a hunt of several
# needs a crowd, a named creature is one body.
RANK_NEEDS_ALIVE = {"normal": 3, "rare": 1, "elite": 1, "rare elite": 1}
# An elite is fair for one player only this far below them.
SOLO_ELITE_MARGIN = 4


def targets(who, party=(), skip=(), limit=14, far=True):
    """Creatures the character may fairly hunt, near and far, each labelled.

    Two tiers: `near` is within 400 yards, `far` reaches 1,500 on the same
    continent, so a bounty can send someone on a journey. Every row carries its
    rank, how many are alive, and the distance and compass bearing of the
    nearest one, so quest text can say "north-east along the coast".

    `alive` discounts spawns that are dead and waiting on their respawn timer.
    A creature that counts this character's race as a friend is never offered,
    and nor is a world boss.

    party: the other members from `parties()`. Their presence is what makes an
           elite fair; alone, an elite is offered only well below level.
    skip:  creature ids to leave out, so a bounty does not repeat its target.
    """
    level = who["level"]
    reach = FAR_YARDS if far else NEAR_YARDS
    widest = max(below for below, _ in RANK_WINDOW.values())
    highest = max(above for _, above in RANK_WINDOW.values())
    found = rows(f"""
        SELECT JSON_OBJECT('creature', Entry, 'name', Name, 'rank', `Rank`, 'loot', LootId,
                           'min_level', MinLevel, 'max_level', MaxLevel, 'faction', Faction,
                           'spawned', spawned, 'alive', alive,
                           'distance', ROUND(distance), 'x', x, 'y', y)
        FROM (
            SELECT t.Entry, t.Name, t.`Rank`, t.LootId, t.MinLevel, t.MaxLevel, t.Faction,
                   ROUND(c.position_x, 1) AS x, ROUND(c.position_y, 1) AS y,
                   SQRT(POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2)) AS distance,
                   COUNT(*) OVER (PARTITION BY t.Entry) AS spawned,
                   SUM(r.guid IS NULL) OVER (PARTITION BY t.Entry) AS alive,
                   ROW_NUMBER() OVER (PARTITION BY t.Entry
                                      ORDER BY POW(c.position_x - ({who['x']}), 2)
                                             + POW(c.position_y - ({who['y']}), 2)) AS nearest
            FROM {hot_quest.WORLD_DB}.creature c
            JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = c.id
            LEFT JOIN {hot_quest.CHAR_DB}.creature_respawn r ON r.guid = c.guid AND r.respawntime > UNIX_TIMESTAMP()
            WHERE c.map = {who['map']}
              AND POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2) < {reach * reach}
              AND t.NpcFlags = 0 AND t.Civilian = 0 AND t.`Rank` <> 3
              AND (t.ExtraFlags & {GUARD_FLAG}) = 0
              AND t.CreatureType NOT IN {SKIP_CREATURE_TYPES}
              AND t.MaxLevel BETWEEN {max(1, level - widest)} AND {level + highest}
        ) spawns
        WHERE nearest = 1
        ORDER BY distance;""")

    tables = factions.get()
    helpers = [other for other in party if other.get("online")]
    leave_out = {int(ident) for ident in skip}
    fair = []
    for row in found:                   # the database returns aggregates as text
        for key in ("spawned", "alive", "distance", "loot"):
            row[key] = int(float(row[key]))
        row["rank"] = RANK_NAMES.get(int(row["rank"]))
        if row["rank"] is None or row["creature"] in leave_out:
            continue
        friendly = tables.is_friendly(row["faction"], who["race"]) if tables else row["faction"] in PLAYER_FACTIONS
        if friendly or row["alive"] < RANK_NEEDS_ALIVE[row["rank"]]:
            continue
        # A hunt of several needs a creature that drops something. A named one
        # is worth facing whether or not it has a loot table of its own.
        if row["rank"] == "normal" and not row["loot"]:
            continue
        below, above = RANK_WINDOW[row["rank"]]
        if not max(1, level - below) <= row["max_level"] <= level + above:
            continue
        row["needs_party"] = False
        if row["rank"] in ("elite", "rare elite"):
            if level - row["max_level"] >= SOLO_ELITE_MARGIN:
                pass                                  # far enough below to take alone
            elif helpers:
                row["needs_party"] = True             # fair only while the party holds
            else:
                continue
        row["tier"] = "near" if row["distance"] <= NEAR_YARDS else "far"
        row["unique"] = row["spawned"] == 1
        row["direction"] = bearing(who, float(row.pop("x")), float(row.pop("y")))
        fair.append(row)
    return trim(fair, limit)


def trim(found, limit):
    """Keep the list short without letting the far tier crowd out what is near."""
    near = [row for row in found if row["tier"] == "near"]
    distant = [row for row in found if row["tier"] == "far"]
    room = max(limit // 3, limit - len(near))
    return near[:limit - min(len(distant), room)] + distant[:room]


def party_note(who, party):
    """One line about the company this character keeps, for the prompt."""
    helpers = [other for other in party if other.get("online")]
    if not helpers:
        return f"{who['name']} is alone."
    levels = ", ".join(f"{other['name']}, level {other['level']}" for other in helpers)
    return f"{who['name']} is in a party of {len(helpers) + 1}, with {levels}."


def givers(who, limit=8):
    """Nearby NPCs the Overseer could speak through: alive, able to give quests, and not hostile to this character.

    The search widens until it finds some. With the game's faction files, any
    non-hostile quest giver qualifies; without them, only NPCs of the
    "friendly to all" faction do.
    """
    tables = factions.get()
    for radius in (300, 800, 2000, 6000):
        found = rows(f"""
            SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'title', IFNULL(t.SubName, ''), 'faction', t.Faction,
                               'flags', t.NpcFlags, 'level', t.MaxLevel, 'gender', mi.gender,
                               'x', ROUND(c.position_x, 1), 'y', ROUND(c.position_y, 1),
                               'spawns', (SELECT COUNT(*) FROM {hot_quest.WORLD_DB}.creature c2 WHERE c2.id = t.Entry))
            FROM {hot_quest.WORLD_DB}.creature c
            JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = c.id
            LEFT JOIN {hot_quest.WORLD_DB}.creature_model_info mi ON mi.modelid = t.DisplayId1
            LEFT JOIN {hot_quest.CHAR_DB}.creature_respawn r ON r.guid = c.guid AND r.respawntime > UNIX_TIMESTAMP()
            WHERE c.map = {who['map']} AND (t.NpcFlags & 2) <> 0 AND r.guid IS NULL
              AND POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2) < {radius * radius}
            ORDER BY POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2)
            LIMIT 80;""")
        chosen, seen = [], set()
        for row in found:
            if row["creature"] in seen or int(row["spawns"]) != 1:      # one spawn, so "return to X" is unambiguous
                continue
            if tables:
                if tables.is_hostile(row["faction"], who["race"]) is not False:
                    continue
            elif row["faction"] != 35:
                continue
            seen.add(row["creature"])
            row["distance"] = round(math.hypot(row["x"] - who["x"], row["y"] - who["y"]))
            row["direction"] = bearing(who, row["x"], row["y"])
            chosen.append(dict({key: row[key] for key in ("creature", "name", "title", "level", "distance", "direction")},
                               sex=SEXES.get(row["gender"], ""), facts=herald_facts(row)))
            if len(chosen) == limit:
                break
        if chosen:
            return chosen
    return []



# ---- how a herald talks ------------------------------------------------------
# A herald's voice is taken from their own stock lines, which the world
# database already holds: a greeting, the text of quests they give and take,
# and their small talk. See docs/proposal_herald_voice.md.

SEXES = {0: "male", 1: "female"}
NPC_ROLES = ((4, "vendor"), (8, "flight master"), (16, "trainer"), (64, "stable master"), (128, "innkeeper"),
             (256, "banker"), (4096, "repairer"))
LINE_KINDS = ("greeting", "offering a quest", "at a turn-in", "small talk")     # in order of preference
LINE_LIMITS = {"offering a quest": 2}           # every other kind gives at most one line
LINE_CHARS = 260
LINE_MIN_CHARS = 30
STOCK_QUESTS_BELOW = hot_quest.QUEST_ID_RANGE[0]    # the DM's own quests are not the NPC's voice


def herald_facts(row):
    """'Stormwind guard, innkeeper, male, level 20': title, roles, sex and level, whichever are known."""
    title = row.get("title") or ""
    roles = " and ".join(name for bit, name in NPC_ROLES
                         if int(row.get("flags") or 0) & bit and name not in title.lower())
    level = f"level {row['level']}" if row.get("level") else ""
    return ", ".join(part for part in (title, roles, SEXES.get(row.get("gender"), ""), level) if part)


def clean_line(text, limit=LINE_CHARS):
    """One line on one row, $B paragraph marks spaced out, cut at a sentence end near the limit.

    $N, $C and $R stay: the model writes the same tokens and knows what they stand for.
    """
    text = " ".join(re.sub(r"\$[Bb]", " ", str(text or "")).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > 0:
        return cut[:end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:-") + "..."


def pick_lines(lines, samples=3):
    """Choose up to `samples` of an NPC's lines: [{"src", "text"}] in, the same shape out.

    Greetings first, then quest offers, turn-in text and small talk. At most two
    quest offers and one of each other kind. Lines that open the same way are
    near-duplicates (one NPC's four class letters), so only the longest is kept.
    """
    picked, seen = [], set()
    order = {kind: i for i, kind in enumerate(LINE_KINDS)}
    for line in sorted(lines, key=lambda l: (order.get(l["src"], len(order)), -len(str(l["text"] or "")))):
        if len(picked) == samples:
            break
        text = clean_line(line["text"])
        opening = re.sub(r"[^a-z]", "", text.lower())[:50]
        if len(text) < LINE_MIN_CHARS or opening in seen:
            continue
        if sum(1 for p in picked if p["src"] == line["src"]) >= LINE_LIMITS.get(line["src"], 1):
            continue
        seen.add(opening)
        picked.append({"src": line["src"], "text": text})
    return picked


def herald_lines(entries, samples=3):
    """{creature id: chosen lines} for several NPCs, in one query. An NPC with no lines maps to []."""
    ids = sorted({int(entry) for entry in entries})
    if not ids:
        return {}
    listed = ", ".join(str(entry) for entry in ids)
    db = hot_quest.WORLD_DB
    found = rows(f"""
        SELECT JSON_OBJECT('creature', g.Entry, 'src', 'greeting', 'text', g.Text) FROM {db}.questgiver_greeting g
          WHERE g.Entry IN ({listed}) AND g.Type = 0
        UNION ALL
        SELECT JSON_OBJECT('creature', r.id, 'src', 'offering a quest', 'text', q.Details) FROM {db}.creature_questrelation r
          JOIN {db}.quest_template q ON q.entry = r.quest
          WHERE r.id IN ({listed}) AND r.quest < {STOCK_QUESTS_BELOW} AND CHAR_LENGTH(q.Details) >= {LINE_MIN_CHARS}
        UNION ALL
        SELECT JSON_OBJECT('creature', r.id, 'src', 'at a turn-in', 'text', q.OfferRewardText) FROM {db}.creature_involvedrelation r
          JOIN {db}.quest_template q ON q.entry = r.quest
          WHERE r.id IN ({listed}) AND r.quest < {STOCK_QUESTS_BELOW} AND CHAR_LENGTH(q.OfferRewardText) >= {LINE_MIN_CHARS}
        UNION ALL
        SELECT JSON_OBJECT('creature', t.Entry, 'src', 'small talk',
                           'text', IF(CHAR_LENGTH(IFNULL(b.Text, '')) > 0, b.Text, b.Text1))
          FROM {db}.creature_template t
          JOIN {db}.gossip_menu m ON m.entry = t.GossipMenuId
          JOIN {db}.npc_text_broadcast_text n ON n.Id = m.text_id
          JOIN {db}.broadcast_text b ON b.Id = n.BroadcastTextId0
          WHERE t.Entry IN ({listed}) AND t.GossipMenuId <> 0;""")
    grouped = {entry: [] for entry in ids}
    for row in found:
        grouped.setdefault(int(row["creature"]), []).append(row)
    return {entry: pick_lines(lines, samples) for entry, lines in grouped.items()}


def herald_voice(entry):
    """One NPC's facts and lines, for the owner's `dm.py voice`. None if there is no such creature."""
    found = rows(f"""
        SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'title', IFNULL(t.SubName, ''), 'flags', t.NpcFlags,
                           'level', t.MaxLevel, 'gender', mi.gender)
        FROM {hot_quest.WORLD_DB}.creature_template t
        LEFT JOIN {hot_quest.WORLD_DB}.creature_model_info mi ON mi.modelid = t.DisplayId1
        WHERE t.Entry = {int(entry)};""")
    if not found:
        return None
    who = found[0]
    return {"creature": who["creature"], "name": who["name"], "facts": herald_facts(who),
            "lines": herald_lines([who["creature"]]).get(who["creature"], [])}


def heralds_named(name):
    """Quest-givers with exactly one spawn whose name contains `name`, an exact match first."""
    name = " ".join(str(name).split())
    like = hot_quest.sql_text("%" + re.sub(r"([\\%_])", r"\\\1", name) + "%")
    return rows(f"""
        SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'title', IFNULL(t.SubName, ''))
        FROM {hot_quest.WORLD_DB}.creature_template t
        WHERE (t.NpcFlags & 2) <> 0 AND t.Name LIKE {like}
          AND (SELECT COUNT(*) FROM {hot_quest.WORLD_DB}.creature c WHERE c.id = t.Entry) = 1
        ORDER BY t.Name = {hot_quest.sql_text(name)} DESC, t.Name LIMIT 10;""")

PROP_ID_RANGE = (200000, 200199)        # ai_dm_spec.md reserves this for DM items


def props():
    """The DM's own story props, read from the game rather than listed here.

    Created once by `server/dm_props.sql`, which needs a server restart. The
    DM adds one to a creature's loot as a quest-only drop while a trophy
    bounty is out, and deletes the row afterwards.
    """
    low, high = PROP_ID_RANGE
    return rows(f"""
        SELECT JSON_OBJECT('item', entry, 'name', name)
        FROM {hot_quest.WORLD_DB}.item_template
        WHERE entry BETWEEN {low} AND {high}
        ORDER BY entry;""")


def carrying(guid, items):
    """How many of each of these items the character already holds.

    A trophy objective would be part-finished the moment it was offered if a
    stack were left over from an earlier bounty, so the validator checks this.
    """
    if not items:
        return {}
    wanted = ", ".join(str(int(item)) for item in items)
    found = rows(f"""
        SELECT JSON_OBJECT('item', ci.item_template, 'count', SUM(ii.count))
        FROM {hot_quest.CHAR_DB}.character_inventory ci
        JOIN {hot_quest.CHAR_DB}.item_instance ii ON ii.guid = ci.item
        WHERE ci.guid = {int(guid)} AND ci.item_template IN ({wanted})
        GROUP BY ci.item_template;""")
    return {int(row["item"]): int(row["count"]) for row in found}


def reward_items(who, reach=10, limit=6):
    """Capstone spell books this character can use and does not already know.

    `reach` lets a book be up to that many levels above the character, so a
    reward can be something to grow into.
    """
    mask = 1 << (who["class"] - 1)
    return rows(f"""
        SELECT JSON_OBJECT('item', i.entry, 'name', i.name, 'required_level', i.RequiredLevel)
        FROM {hot_quest.WORLD_DB}.item_template i
        JOIN {hot_quest.WORLD_DB}.spell_template teach ON teach.Id = i.spellid_1
        WHERE i.entry >= 100000 AND i.Quality = 3
          AND (i.AllowableClass = -1 OR (i.AllowableClass & {mask}) <> 0)
          AND i.RequiredLevel <= {who['level'] + reach}
          AND NOT EXISTS (SELECT 1 FROM {hot_quest.CHAR_DB}.character_spell s
                          WHERE s.guid = {who['guid']} AND s.spell = teach.EffectTriggerSpell1)
        ORDER BY i.RequiredLevel, i.name
        LIMIT {limit};""")


# ---- gear rewards (docs/proposal_gear_rewards.md) ---------------------------

# The skill an item needs, by subclass, from Item::GetSkill.
WEAPON_SKILL = {0: 44, 1: 172, 2: 45, 3: 46, 4: 54, 5: 160, 6: 229, 7: 43, 8: 55, 10: 136, 13: 162, 15: 173,
                16: 176, 18: 226, 19: 228}
ARMOR_SKILL = {1: 415, 2: 414, 3: 413, 4: 293, 6: 433}     # cloth, leather, mail, plate, shield
BODY_ARMOR = (4, 3, 2, 1)                                   # plate, mail, leather, cloth: heaviest first
CLOAK_SLOT = 16
STAT = {3: "Agility", 4: "Strength", 5: "Intellect", 6: "Spirit", 7: "Stamina"}
# Stats that count for each class. An item whose largest stat is not here is
# no use to them. A heuristic, to tune in play.
WANTED = {1: {4, 7, 3}, 2: {4, 7, 5, 6}, 3: {3, 7, 5}, 4: {3, 7, 4}, 5: {5, 6, 7}, 7: {5, 7, 4, 3, 6},
          8: {5, 6, 7}, 9: {5, 7, 6}, 11: {5, 6, 7, 4, 3}}
SLOT = {1: "head", 2: "neck", 3: "shoulder", 5: "chest", 6: "waist", 7: "legs", 8: "feet", 9: "wrist", 10: "hands",
        11: "finger", 12: "trinket", 13: "one-hand", 14: "shield", 15: "ranged", 16: "back", 17: "two-hand",
        20: "chest", 21: "main hand", 22: "off hand", 23: "held in off-hand", 25: "thrown", 26: "ranged"}
WEAPON_NAME = {0: "axe", 1: "two-handed axe", 2: "bow", 3: "gun", 4: "mace", 5: "two-handed mace", 6: "polearm",
               7: "sword", 8: "two-handed sword", 10: "staff", 13: "fist weapon", 15: "dagger", 16: "thrown",
               18: "crossbow", 19: "wand"}
# Not shirts, bags, tabards, ammo, quivers or relics.
SKIP_SLOTS = (0, 4, 18, 19, 24, 27, 28)
# tier: (quality, levels below the character, limit). A prize's reach above
# the character is DM_PRIZE_REACH; standard gear is never above.
GEAR_TIERS = {"standard": (2, 4, 8), "prize": (3, 2, 6)}


def prize_reach():
    console.load_env()
    try:
        return int(os.environ.get("DM_PRIZE_REACH", 5))
    except ValueError:
        return 5


def gear_window(level, tier):
    """The power levels a tier may offer at this character level, as (low, high)."""
    _, below, _ = GEAR_TIERS[tier]
    return max(1, level - below), level + (prize_reach() if tier == "prize" else 0)


def gear(who, tier):
    """Weapons and armor this character can use, for one reward tier.

    standard: uncommon, up to 4 levels below the character and never above.
    prize:    rare, from 2 below to DM_PRIZE_REACH above. No rare gear exists
              below power level 15, so low characters get an empty list.

    Only items something in the world drops, sells or rewards, with fixed stats,
    and not already held by the character.
    """
    quality, _, limit = GEAR_TIERS[tier]
    low, high = gear_window(who["level"], tier)
    w, c = hot_quest.WORLD_DB, hot_quest.CHAR_DB
    power = "IF(i.RequiredLevel > 0, i.RequiredLevel, GREATEST(1, CAST(i.ItemLevel AS SIGNED) - 5))"
    stats = ", ".join(f"'t{n}', i.stat_type{n}, 'v{n}', i.stat_value{n}" for n in range(1, 11))
    sources = " UNION ".join(
        [f"SELECT item FROM {w}.{table}" for table in
         ("creature_loot_template", "reference_loot_template", "gameobject_loot_template", "npc_vendor")]
        + [f"SELECT {column} FROM {w}.quest_template" for column in
           ("RewItemId1", "RewItemId2") + tuple(f"RewChoiceItemId{n}" for n in range(1, 7))])
    found = rows(f"""
        SELECT JSON_OBJECT('item', i.entry, 'name', i.name, 'class', i.class, 'subclass', i.subclass,
                           'slot', i.InventoryType, 'required_level', i.RequiredLevel, 'level', {power}, {stats})
        FROM {w}.item_template i
        JOIN ({sources}) real_item ON real_item.item = i.entry
        WHERE i.entry < 90000 AND i.class IN (2, 4) AND i.Quality = {quality} AND i.RandomProperty = 0
          AND {power} BETWEEN {low} AND {high}
          AND (i.AllowableClass = -1 OR (i.AllowableClass & {1 << (who['class'] - 1)}) <> 0)
          AND (i.AllowableRace = -1 OR (i.AllowableRace & {1 << (who['race'] - 1)}) <> 0)
          AND i.RequiredSkill = 0 AND i.requiredhonorrank = 0 AND i.RequiredReputationFaction = 0
          AND i.InventoryType NOT IN {SKIP_SLOTS}
          AND NOT EXISTS (SELECT 1 FROM {c}.character_inventory ci
                          WHERE ci.guid = {int(who['guid'])} AND ci.item_template = i.entry);""")
    return fit_gear(found, skills(who["guid"]), who["class"], limit)


def skills(guid):
    """The skill ids a character has, weapon and armor skills among them."""
    return {int(row["skill"]) for row in rows(f"""
        SELECT JSON_OBJECT('skill', skill) FROM {hot_quest.CHAR_DB}.character_skills WHERE guid = {int(guid)};""")}


def fit_gear(found, known, char_class, limit):
    """Keep what this character can use and would want, one item per kind.

    found: item rows from the catalogue query. known: the character's skill ids.
    Weapons come first, then the highest power level.
    """
    best_armor = next((sub for sub in BODY_ARMOR if ARMOR_SKILL[sub] in known), 1)
    fit = []
    for it in found:
        sub = it["subclass"]
        if it["class"] == 2:
            need = WEAPON_SKILL.get(sub)
            if not need or need not in known:
                continue
            kind = WEAPON_NAME[sub]
        else:
            body = sub in (1, 2, 3, 4) and it["slot"] != CLOAK_SLOT
            if body:
                if sub != best_armor:           # only the heaviest armor the character wears
                    continue
            elif sub == 6:
                if ARMOR_SKILL[6] not in known:
                    continue
            elif sub not in (0, 1):             # librams, idols, totems are left to class limits
                continue
            kind = SLOT.get(it["slot"], "armor")
            if body:
                kind += " armor"
        stats = sorted(((it[f"v{n}"], it[f"t{n}"]) for n in range(1, 11)
                        if it.get(f"t{n}") in STAT and it.get(f"v{n}", 0) > 0), reverse=True)
        if stats and stats[0][1] not in WANTED.get(char_class, set(STAT)):
            continue                            # its main stat is no use to this class
        fit.append({"item": it["item"], "name": it["name"], "kind": kind, "weapon": it["class"] == 2,
                    "level": int(it["level"]), "required_level": it["required_level"],
                    "stats": ", ".join(f"+{v} {STAT[t]}" for v, t in stats) or "no stats"})
    fit.sort(key=lambda g: (not g["weapon"], -g["level"], g["name"]))
    picked, seen = [], set()
    for g in fit:                               # one per kind, so the list has variety
        if g["kind"] in seen:
            continue
        seen.add(g["kind"])
        picked.append(g)
        if len(picked) == limit:
            break
    return picked


def xp_weight(level):
    """The stock table's XP input for a quest of this level: the stock average."""
    found = rows(f"""
        SELECT JSON_OBJECT('weight', ROUND(AVG(RewMoneyMaxLevel)))
        FROM {hot_quest.WORLD_DB}.quest_template
        WHERE QuestLevel = {int(level)} AND RewMoneyMaxLevel > 0;""")
    return int(found[0]["weight"] or 0) if found else 0


def next_quest_id():
    """Lowest id in the DM range that no quest, past or present, has used."""
    low, high = hot_quest.QUEST_ID_RANGE
    found = rows(f"""
        SELECT JSON_OBJECT('next', GREATEST(
            IFNULL((SELECT MAX(entry) FROM {hot_quest.WORLD_DB}.quest_template WHERE entry BETWEEN {low} AND {high}), {low - 1}),
            IFNULL((SELECT MAX(quest) FROM {hot_quest.CHAR_DB}.character_queststatus WHERE quest BETWEEN {low} AND {high}), {low - 1})
        ) + 1);""")
    return int(found[0]["next"])


# ---- observation: what the story loop watches -----------------------------

# Zone ids the character table reports, for readable story text. Unknown ids
# fall back to "zone <id>".
ZONES = {
    1: "Dun Morogh", 3: "Badlands", 4: "Blasted Lands", 8: "Swamp of Sorrows", 10: "Duskwood", 11: "Wetlands",
    12: "Elwynn Forest", 14: "Durotar", 15: "Dustwallow Marsh", 16: "Azshara", 17: "The Barrens",
    28: "Western Plaguelands", 33: "Stranglethorn Vale", 36: "Alterac Mountains", 38: "Loch Modan", 40: "Westfall",
    41: "Deadwind Pass", 44: "Redridge Mountains", 45: "Arathi Highlands", 46: "Burning Steppes",
    47: "The Hinterlands", 51: "Searing Gorge", 85: "Tirisfal Glades", 130: "Silverpine Forest",
    139: "Eastern Plaguelands", 141: "Teldrassil", 148: "Darkshore", 215: "Mulgore", 267: "Hillsbrad Foothills",
    331: "Ashenvale", 357: "Feralas", 361: "Felwood", 400: "Thousand Needles", 405: "Desolace",
    406: "Stonetalon Mountains", 440: "Tanaris", 490: "Un'Goro Crater", 493: "Moonglade", 618: "Winterspring",
    1377: "Silithus", 1497: "Undercity", 1519: "Stormwind City", 1537: "Ironforge", 1637: "Orgrimmar",
    1638: "Thunder Bluff", 1657: "Darnassus",
}

# Capstone books are named by class; a spell from another class's book is a
# story event, a spell from your own is ordinary training.
BOOK_PREFIX = {2: "Libram of", 5: "Codex of", 7: "Tablet of", 8: "Tome of", 9: "Grimoire of", 11: "Book of"}


def zone_name(zone):
    return ZONES.get(zone, f"zone {zone}")


# ---- a zone's own story -------------------------------------------------------
# The stock quests filed under a zone are its canonical story and cast. See
# docs/proposal_campaign_layer.md, 4.2.

CAPITALS = frozenset((1497, 1519, 1537, 1637, 1638, 1657))     # interludes: no chapter of their own
SIDE_RACES = {"alliance": 1 | 4 | 8 | 64, "horde": 2 | 16 | 32 | 128}   # RequiredRaces masks
SIDE_MODEL_RACE = {"alliance": 1, "horde": 2}       # a race to ask the faction files about, per side
DOSSIER_LIMITS = {"quests": 24, "cast": 14, "enemies": 16}
DOSSIER_MIN_QUESTS = 10                              # fewer than this and a zone gets no chapter


def side(race):
    """'alliance' or 'horde' for a race id."""
    return "alliance" if (1 << (int(race) - 1)) & SIDE_RACES["alliance"] else "horde"


def fetch_dossier(zone, side_name):
    """The raw rows behind a zone's dossier, for one side: quests the other side cannot take are left out."""
    zone, mask = int(zone), SIDE_RACES[side_name]
    stock = (f"q.ZoneOrSort = {zone} AND q.entry < {STOCK_QUESTS_BELOW} "
             f"AND (q.RequiredRaces = 0 OR (q.RequiredRaces & {mask}) <> 0)")
    span = rows(f"""SELECT JSON_OBJECT('quests', COUNT(*), 'low', MIN(q.QuestLevel), 'high', MAX(q.QuestLevel))
                    FROM {hot_quest.WORLD_DB}.quest_template q WHERE {stock} AND q.QuestLevel > 0;""")[0]
    lines = rows(f"""SELECT JSON_OBJECT('title', q.Title, 'level', MIN(q.QuestLevel))
                     FROM {hot_quest.WORLD_DB}.quest_template q WHERE {stock} AND q.QuestLevel > 0
                     GROUP BY q.Title ORDER BY MIN(q.QuestLevel), MIN(q.entry)
                     LIMIT {DOSSIER_LIMITS['quests']};""")
    cast = rows(f"""SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'title', IFNULL(t.SubName, ''),
                                       'faction', t.Faction, 'quests', COUNT(DISTINCT q.entry))
                    FROM {hot_quest.WORLD_DB}.quest_template q
                    JOIN {hot_quest.WORLD_DB}.creature_questrelation r ON r.quest = q.entry
                    JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = r.id
                    WHERE {stock}
                    GROUP BY t.Entry ORDER BY COUNT(DISTINCT q.entry) DESC, t.Name
                    LIMIT {DOSSIER_LIMITS['cast'] * 2};""")
    foes = rows(f"""SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'low', t.MinLevel, 'high', t.MaxLevel,
                                       'notable', t.`Rank` > 0)
                    FROM {hot_quest.WORLD_DB}.quest_template q
                    JOIN {hot_quest.WORLD_DB}.creature_template t
                      ON t.Entry IN (q.ReqCreatureOrGOId1, q.ReqCreatureOrGOId2, q.ReqCreatureOrGOId3, q.ReqCreatureOrGOId4)
                    WHERE {stock}
                    GROUP BY t.Entry ORDER BY t.MinLevel, t.Name LIMIT {DOSSIER_LIMITS['enemies']};""")
    return span, lines, cast, foes


def shape_dossier(zone, side_name, span, lines, cast, foes, friendly=lambda faction: True):
    """A dossier from its raw rows: deduplicated, limited, and without givers hostile to this side."""
    titles, seen = [], set()
    for row in lines:
        if row["title"] not in seen:
            seen.add(row["title"])
            titles.append({"title": row["title"], "level": int(row["level"])})
    people = [{key: row[key] for key in ("creature", "name", "title", "quests")}
              for row in cast if friendly(row.get("faction"))]
    enemies = [{"creature": row["creature"], "name": row["name"], "low": int(row["low"]), "high": int(row["high"]),
                "notable": bool(row["notable"])} for row in foes]
    return {"zone": int(zone), "side": side_name, "quests": int(span.get("quests") or 0),
            "low": int(span.get("low") or 0), "high": int(span.get("high") or 0),
            "quest_lines": titles[:DOSSIER_LIMITS["quests"]], "cast": people[:DOSSIER_LIMITS["cast"]],
            "enemies": enemies[:DOSSIER_LIMITS["enemies"]]}


def zone_dossier(zone, side_name):
    """A zone's own story, as one side sees it: its quests, who gives them, and what they send players against."""
    tables = factions.get()
    race = SIDE_MODEL_RACE[side_name]
    friendly = (lambda faction: tables.is_hostile(faction, race) is False) if tables else (lambda faction: True)
    return shape_dossier(zone, side_name, *fetch_dossier(zone, side_name), friendly=friendly)


def dossier_text(d):
    """A dossier as prompt text, with creature ids so the model can name them."""
    if not d["quests"]:
        return f"{zone_name(d['zone'])}: no stock quests for this side."
    people = "; ".join(f"{c['creature']} {c['name']}" + (f" <{c['title']}>" if c["title"] else "") + f" ({c['quests']})"
                       for c in d["cast"])
    foes = "; ".join(f"{e['creature']} {e['name']} ({e['low']}-{e['high']}{', notable' if e['notable'] else ''})"
                     for e in d["enemies"])
    return "\n".join([
        f"{zone_name(d['zone'])}: {d['quests']} stock quests, levels {d['low']} to {d['high']}.",
        "Its quests, in level order: " + "; ".join(q["title"] for q in d["quest_lines"]) + ".",
        "Who gives them (id, name, quests given): " + (people or "nobody listed") + ".",
        "What they send players against (id, name, levels): " + (foes or "nothing listed") + "."])


def online_characters():
    """Every character currently logged in."""
    found = rows(f"""
        SELECT JSON_OBJECT('guid', guid, 'name', name, 'race', race, 'class', class, 'level', level,
                           'map', map, 'zone', zone, 'x', ROUND(position_x, 1), 'y', ROUND(position_y, 1),
                           'online', online, 'money', money)
        FROM {hot_quest.CHAR_DB}.characters WHERE online = 1 ORDER BY guid;""")
    for who in found:
        who["race_name"] = RACES.get(who["race"], str(who["race"]))
        who["class_name"] = CLASSES.get(who["class"], str(who["class"]))
    return found


def describe(who):
    """'Ralf, a level 5 Human Rogue' from a character row."""
    return (f"{who['name']}, a level {who['level']} {RACES.get(who['race'], who['race'])} "
            f"{CLASSES.get(who['class'], who['class'])}")


def parties():
    """Who is in a party with whom right now: {guid: [the other members]}.

    The server writes party membership to the database as it changes, so this
    is current without a forced save. Members who are offline are included.
    """
    found = rows(f"""
        SELECT JSON_OBJECT('party', gm.groupId, 'guid', c.guid, 'name', c.name, 'race', c.race,
                           'class', c.class, 'level', c.level, 'online', c.online)
        FROM {hot_quest.CHAR_DB}.group_member gm
        JOIN {hot_quest.CHAR_DB}.characters c ON c.guid = gm.memberGuid;""")
    by_party = {}
    for member in found:
        by_party.setdefault(member["party"], []).append(member)
    return {member["guid"]: [other for other in members if other["guid"] != member["guid"]]
            for members in by_party.values() for member in members}


def nearby(who, others, yards=150):
    """Online characters on the same map within `yards` of this one."""
    return [other for other in others
            if other["guid"] != who["guid"] and other["map"] == who["map"]
            and (other["x"] - who["x"]) ** 2 + (other["y"] - who["y"]) ** 2 <= yards * yards]


def rewarded_quests(guid):
    """Quests this character has turned in: {quest id: title}."""
    found = rows(f"""
        SELECT JSON_OBJECT('quest', s.quest, 'title', IFNULL(q.Title, CONCAT('quest ', s.quest)))
        FROM {hot_quest.CHAR_DB}.character_queststatus s
        LEFT JOIN {hot_quest.WORLD_DB}.quest_template q ON q.entry = s.quest
        WHERE s.guid = {int(guid)} AND s.rewarded = 1;""")
    return {row["quest"]: row["title"] for row in found}


def dm_quest_progress():
    """Every character's state on every DM quest.

    state: 'accepted' (in the log), 'ready' (objectives done), 'done' (turned in).
    """
    low, high = hot_quest.QUEST_ID_RANGE
    return rows(f"""
        SELECT JSON_OBJECT('guid', s.guid, 'name', c.name, 'quest', s.quest,
                           'state', IF(s.rewarded = 1, 'done', IF(s.status = 1, 'ready', 'accepted')))
        FROM {hot_quest.CHAR_DB}.character_queststatus s
        JOIN {hot_quest.CHAR_DB}.characters c ON c.guid = s.guid
        WHERE s.quest BETWEEN {low} AND {high} AND (s.rewarded = 1 OR s.status IN (1, 3));""")


def borrowed_capstones(who):
    """Capstone-book spells this character knows that belong to another class."""
    own = BOOK_PREFIX.get(who["class"])
    not_own = f"AND i.name NOT LIKE '{own} %'" if own else ""
    found = rows(f"""
        SELECT DISTINCT JSON_OBJECT('spell', taught.Id, 'name', taught.SpellName)
        FROM {hot_quest.WORLD_DB}.item_template i
        JOIN {hot_quest.WORLD_DB}.spell_template teach ON teach.Id = i.spellid_1
        JOIN {hot_quest.WORLD_DB}.spell_template taught ON taught.Id = teach.EffectTriggerSpell1
        JOIN {hot_quest.CHAR_DB}.character_spell s ON s.spell = taught.Id AND s.guid = {int(who['guid'])}
        WHERE i.entry >= 100000 AND i.Quality = 3 {not_own};""")
    return {row["spell"]: row["name"] for row in found}


def letters_to(name):
    """Mail players have sent to the DM's own character, oldest first."""
    if not re.fullmatch(r"[A-Za-z]{2,12}", name or ""):
        return []
    return rows(f"""
        SELECT JSON_OBJECT('id', m.id, 'from_guid', m.sender, 'from', c.name, 'subject', m.subject,
                           'body', IFNULL(t.text, ''))
        FROM {hot_quest.CHAR_DB}.mail m
        JOIN {hot_quest.CHAR_DB}.characters dm ON dm.guid = m.receiver AND dm.name = '{name}'
        JOIN {hot_quest.CHAR_DB}.characters c ON c.guid = m.sender
        LEFT JOIN {hot_quest.CHAR_DB}.item_text t ON t.id = m.itemTextId
        WHERE m.messageType = 0
        ORDER BY m.id;""")


def money_cap(level):
    """Most copper a single DM quest may pay at this level."""
    return 50 * level * level + 200


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        who = character(sys.argv[1])
        if not who:
            sys.exit(f"world_query: no character named {sys.argv[1]}")
        party = parties().get(who["guid"], [])
        report = {"character": who, "faction_files_found": factions.get() is not None,
                  "party": party,
                  "givers": givers(who), "targets": targets(who, party=party),
                  "reward_items": reward_items(who),
                  "gear_standard": gear(who, "standard"), "gear_prize": gear(who, "prize"),
                  "xp_weight": xp_weight(who["level"]),
                  "money_cap_copper": money_cap(who["level"]), "next_quest_id": next_quest_id()}
    except QueryError as error:
        sys.exit(f"world_query: {error}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
