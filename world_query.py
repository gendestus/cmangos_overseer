#!/usr/bin/env python3
"""Read-only questions the DM asks the game databases.

    python3 world_query.py Zachadin        # print everything known for a character

Nothing here writes. Every function returns plain dicts and lists, ready to be
shown to a model or a person.

Settings (environment or the .env beside this file):
    DM_COMPOSE_DIR        folder holding compose.yaml (default ~/cmangos-deploy)
    DM_DB_QUERY_COMMAND   command that reads SQL on stdin and prints raw rows;
                          default runs the mariadb client in the database container
    DM_GIVERS             creature ids allowed to hand out DM quests (default 4991)
"""
import json
import os
import re
import shlex
import subprocess
import sys

import console
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
# Faction templates of the player cities and "friendly to all". The database
# has no hostility table, so this list plus the guard flag is a heuristic; the
# human approval step is the backstop.
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


def targets(who, radius=400, below=3, above=2, limit=12):
    """Hostile-looking creatures spawned near the character and close to its level.

    `alive` discounts spawns that are dead and waiting on their respawn timer.
    """
    level = who["level"]
    found = rows(f"""
        SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name, 'min_level', t.MinLevel, 'max_level', t.MaxLevel,
                           'spawned', COUNT(*), 'alive', SUM(r.guid IS NULL),
                           'distance', ROUND(MIN(SQRT(POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2)))))
        FROM {hot_quest.WORLD_DB}.creature c
        JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = c.id
        LEFT JOIN {hot_quest.CHAR_DB}.creature_respawn r ON r.guid = c.guid AND r.respawntime > UNIX_TIMESTAMP()
        WHERE c.map = {who['map']}
          AND POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2) < {radius * radius}
          AND t.NpcFlags = 0 AND t.LootId <> 0 AND t.Civilian = 0 AND t.`Rank` = 0
          AND (t.ExtraFlags & {GUARD_FLAG}) = 0
          AND t.CreatureType NOT IN {SKIP_CREATURE_TYPES}
          AND t.Faction NOT IN {PLAYER_FACTIONS}
          AND t.MaxLevel BETWEEN {max(1, level - below)} AND {level + above}
        GROUP BY t.Entry
        HAVING SUM(r.guid IS NULL) >= 3
        ORDER BY MIN(SQRT(POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2)))
        LIMIT {limit};""")
    for row in found:                   # the database returns aggregates as text
        for key in ("spawned", "alive", "distance"):
            row[key] = int(float(row[key]))
    return found


def giver(who):
    """The nearest spawned NPC that is allowed to hand out DM quests, or None."""
    allowed = [int(part) for part in os.environ.get("DM_GIVERS", "4991").split(",") if part.strip().isdigit()]
    if not allowed:
        return None
    found = rows(f"""
        SELECT JSON_OBJECT('creature', t.Entry, 'name', t.Name,
                           'distance', ROUND(SQRT(POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2))))
        FROM {hot_quest.WORLD_DB}.creature c
        JOIN {hot_quest.WORLD_DB}.creature_template t ON t.Entry = c.id
        WHERE c.map = {who['map']} AND t.Entry IN ({', '.join(map(str, allowed))}) AND (t.NpcFlags & 2) <> 0
        ORDER BY POW(c.position_x - ({who['x']}), 2) + POW(c.position_y - ({who['y']}), 2)
        LIMIT 1;""")
    if not found:
        return None
    found[0]["distance"] = int(float(found[0]["distance"]))
    return found[0]


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
        SELECT JSON_OBJECT('guid', s.guid, 'name', c.name, 'quest', s.quest, 'kills', s.mobcount1,
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
        report = {"character": who, "giver": giver(who), "targets": targets(who),
                  "reward_items": reward_items(who), "xp_weight": xp_weight(who["level"]),
                  "money_cap_copper": money_cap(who["level"]), "next_quest_id": next_quest_id()}
    except QueryError as error:
        sys.exit(f"world_query: {error}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
