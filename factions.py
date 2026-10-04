#!/usr/bin/env python3
"""Friend or foe, read from the game's own data.

The world database says which faction template a creature belongs to, but not
how that faction feels about a player. That lives in two of the client data
files the server extracts: FactionTemplate.dbc and ChrRaces.dbc. This module
reads both and answers with the same logic the server uses
(FactionTemplateEntry::IsFriendlyTo / IsHostileTo).

    python3 factions.py            # self-check against the real files

Setting:
    DM_DBC_DIR   folder holding the extracted .dbc files. Default:
                 <DM_COMPOSE_DIR>/storage/classic/mangosd/extracted-data/dbc
"""
import os
import struct
import sys

_cache = {}


def dbc_dir():
    explicit = os.environ.get("DM_DBC_DIR")
    if explicit:
        return os.path.expanduser(explicit)
    compose = os.path.expanduser(os.environ.get("DM_COMPOSE_DIR", "~/cmangos-deploy"))
    return os.path.join(compose, "storage", "classic", "mangosd", "extracted-data", "dbc")


def read_dbc(path, wanted_fields):
    """Rows of a .dbc file as tuples of unsigned 32-bit numbers (the first `wanted_fields` columns)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"WDBC":
        raise ValueError(f"{path} is not a DBC file")
    records, fields, record_size, _strings = struct.unpack_from("<4I", data, 4)
    if fields < wanted_fields or record_size < wanted_fields * 4:
        raise ValueError(f"{path} has {fields} fields per row; expected at least {wanted_fields}")
    return [struct.unpack_from(f"<{wanted_fields}I", data, 20 + index * record_size) for index in range(records)]


class Factions:
    """Faction templates and each player race's own template."""

    def __init__(self, templates, race_template):
        self.templates = templates              # id -> dict(faction, ours, friend, enemy, enemies, friends)
        self.race_template = race_template      # race id -> faction template id

    @classmethod
    def load(cls, folder=None):
        folder = folder or dbc_dir()
        templates = {}
        for row in read_dbc(os.path.join(folder, "FactionTemplate.dbc"), 14):
            templates[row[0]] = {"faction": row[1], "ours": row[3], "friend": row[4], "enemy": row[5],
                                 "enemies": [f for f in row[6:10] if f], "friends": [f for f in row[10:14] if f]}
        race_template = {row[0]: row[2] for row in read_dbc(os.path.join(folder, "ChrRaces.dbc"), 3)}
        return cls(templates, race_template)

    def _pair(self, npc_template, race):
        npc = self.templates.get(npc_template)
        player = self.templates.get(self.race_template.get(race))
        return npc, player

    def is_hostile(self, npc_template, race):
        """Would this creature attack a player of this race? None if either side is unknown."""
        npc, player = self._pair(npc_template, race)
        if not npc or not player:
            return None
        if player["faction"]:
            if player["faction"] in npc["enemies"]:
                return True
            if player["faction"] in npc["friends"]:
                return False
        return (npc["enemy"] & player["ours"]) != 0

    def is_friendly(self, npc_template, race):
        """Does this creature count a player of this race as a friend? None if unknown."""
        npc, player = self._pair(npc_template, race)
        if not npc or not player:
            return None
        if player["faction"]:
            if player["faction"] in npc["enemies"]:
                return False
            if player["faction"] in npc["friends"]:
                return True
        return bool((npc["friend"] & player["ours"]) or (npc["ours"] & player["friend"]))


def get():
    """The loaded tables, or None if the files cannot be read. Loaded once per run."""
    folder = dbc_dir()
    if folder not in _cache:
        try:
            _cache[folder] = Factions.load(folder)
        except (OSError, ValueError, struct.error):
            _cache[folder] = None
    return _cache[folder]


def main():
    folder = dbc_dir()
    try:
        tables = Factions.load(folder)
    except (OSError, ValueError, struct.error) as error:
        sys.exit(f"factions: cannot read the faction files in {folder}: {error}")
    print(f"read {len(tables.templates)} faction templates and {len(tables.race_template)} races from {folder}")
    # Faction templates every 1.12 server has: 11 and 12 Stormwind, 29 and 85 Orgrimmar, 35 friendly to all,
    # 14 and 16 monsters.
    print("expected: Stormwind (11, 12) friendly to a Human and hostile to an Orc; Orgrimmar (29, 85) the reverse;")
    print("          35 friendly to both; monsters (14, 16) hostile to both.\n")
    print(f"{'template':>8}  {'to a Human (race 1)':22}  {'to an Orc (race 2)':22}")
    for template in (11, 12, 29, 85, 35, 14, 16):
        words = []
        for race in (1, 2):
            hostile, friendly = tables.is_hostile(template, race), tables.is_friendly(template, race)
            words.append("unknown" if hostile is None else "hostile" if hostile else "friendly" if friendly else "neutral")
        print(f"{template:>8}  {words[0]:22}  {words[1]:22}")


if __name__ == "__main__":
    main()
