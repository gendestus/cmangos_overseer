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
                               'x', ROUND(c.position_x, 1), 'y', ROUND(c.position_y, 1),
                               'spawns', (SELECT COUNT(*) FROM {hot_quest.WORLD_DB}.creature c2 WHERE c2.id = t.Entry))
            FROM {hot_quest.WORLD_DB}.creature c
            JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = c.id
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
            chosen.append({key: row[key] for key in ("creature", "name", "title", "distance", "direction")})
            if len(chosen) == limit:
                break
        if chosen:
            return chosen
    return []


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
                  "reward_items": reward_items(who), "xp_weight": xp_weight(who["level"]),
                  "money_cap_copper": money_cap(who["level"]), "next_quest_id": next_quest_id()}
    except QueryError as error:
        sys.exit(f"world_query: {error}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
