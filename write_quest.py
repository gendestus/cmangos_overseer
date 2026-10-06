#!/usr/bin/env python3
"""Have the model write a bounty for one character, then put it live on approval.

    python3 write_quest.py Zachadin
    python3 write_quest.py Zachadin --hint "something eerie, the Overseer is testing nerve"
    python3 write_quest.py Zachadin --show-context     # print what the model would see; no API call
    python3 write_quest.py Zachadin --dry-run          # call the model, show the quest, apply nothing

What the model decides: the wording, which nearby NPC offers the quest, which
nearby creature to hunt, how many, and the reward, all chosen from lists this
script gives it.
What this script decides: the quest id, the zone, the levels and the XP. It then checks every model choice against the same lists before anything
is written, and asks you before applying.

Needs ANTHROPIC_API_KEY in the environment or the .env beside this file.
"""
import argparse
import json
import math
import os
import sys

import apply_quest
import console
import hot_quest
import llm
import world_query

ISSUED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issued")

MAX_OBJECTIVES = 2          # the quest format allows four; two is enough to start
MAX_EXPECTED_KILLS = 24     # the most kills a trophy objective may imply
MAX_GEAR = 3                # gear ids one bounty may offer; more than one is a choice
VOICED_HERALDS = 5          # the nearest heralds are shown their own lines; the rest only their facts
MAX_VOICE_WORDS = 80        # a voice note is asked for in 40 words; far past that is refused
GEAR_TIERS = ("standard", "prize")


class Kind:
    """One sort of bounty: what it may ask for, and what it is worth.

    objectives   how many objectives the kind allows, as (fewest, most)
    ranks        creature ranks it may target, from world_query.RANK_NAMES
    max_count    ceiling on one objective's count, before the alive count is applied
    money        multiplier on the level's money cap, so harder work pays better
    collects     True if its objectives collect a prop rather than count kills
    rule         the sentence the model is told about this kind
    """
    collects = False

    def __init__(self, objectives, ranks, max_count, money, rule):
        self.objectives, self.ranks = objectives, ranks
        self.max_count, self.money, self.rule = max_count, money, rule

    def objective(self, entry, target, context):
        """Check one objective of this kind. Returns what the spec needs from it."""
        return {}

    def check(self, chosen, context):
        """Rules that need the whole answer, not one objective. Raises Rejected."""


class Journey(Kind):
    def check(self, chosen, context):
        if not any(o["target"]["tier"] == "far" for o in chosen):
            raise Rejected("a journey must send the character to a far target; all of these are nearby")


class Party(Kind):
    def check(self, chosen, context):
        if not online_company(context):
            raise Rejected("a party bounty needs company; this character is alone")


class Trophy(Kind):
    """Collect a DM prop that the target has been made to carry.

    The prop is added to that creature's loot as a quest-only drop while the
    bounty is out, so it drops for whoever needs it and for nobody else, and
    it is deleted again when the bounty ends.
    """
    collects = True

    def objective(self, entry, target, context):
        allowed = {prop["item"]: prop for prop in context.get("props", [])}
        prop = allowed.get(entry.get("prop"))
        if not prop:
            raise Rejected(f"prop {entry.get('prop')} is not in the list of props this bounty may use")
        chance = entry.get("chance")
        if chance not in hot_quest.PROP_CHANCES:
            raise Rejected(f"drop chance {chance!r} must be one of {hot_quest.PROP_CHANCES}")
        count = entry.get("count")
        # The area has to be able to supply the hunt. The drop stops once the
        # objective is met, so this is the worst case, not an average.
        expected = math.ceil(count * 100 / chance) if isinstance(count, int) and count > 0 else 0
        if expected > target["alive"]:
            raise Rejected(f"{count} x {prop['name']} at {chance}% needs about {expected} kills, "
                           f"but only {target['alive']} {target['name']} are alive")
        if expected > MAX_EXPECTED_KILLS:
            raise Rejected(f"{count} x {prop['name']} at {chance}% needs about {expected} kills, "
                           f"more than the {MAX_EXPECTED_KILLS} a bounty may ask for")
        return {"prop": prop["item"], "prop_name": prop["name"], "chance": chance, "expected_kills": expected}


# The one place a kind is defined. The prompt text, the tool's enum and every
# check are all read from here, so they cannot drift apart.
KINDS = {
    "hunt": Kind(
        objectives=(1, 1), ranks=("normal",), max_count=12, money=1.0,
        rule="hunt: kill several of one ordinary creature. The plainest bounty, and the right choice "
             "when nothing else fits."),
    "mark": Kind(
        objectives=(1, 1), ranks=("elite", "rare", "rare elite"), max_count=1, money=2.0,
        rule="mark: one named creature, killed once. Name it in the quest text and write the bounty as a "
             "hunt for that particular enemy, not for a crowd."),
    "journey": Journey(
        objectives=(1, 1), ranks=("normal", "elite", "rare", "rare elite"), max_count=8, money=1.5,
        rule="journey: a hunt or a mark against a target marked \"a journey\". Say in the quest text which "
             "way they must travel, using the direction given."),
    "party": Party(
        objectives=(2, 2), ranks=("normal", "elite", "rare", "rare elite"), max_count=10, money=2.0,
        rule="party: two objectives, written for the whole group rather than one person. Only for a "
             "character who has company."),
    "trophy": Trophy(
        objectives=(1, 1), ranks=("normal", "elite", "rare", "rare elite"), max_count=hot_quest.MAX_PROP_COUNT,
        money=1.5,
        rule="trophy: collect something a creature is carrying, chosen from the list of props. Pick a prop "
             "that makes sense as a thing that creature would own or have taken, and say in the quest text "
             "why the Overseer wants it. Every party member can loot their own copy."),
}


def online_company(context):
    return [other for other in context.get("party", []) if other.get("online")]


def kind_rules():
    """The kinds, as lines for the prompt."""
    return "\n".join(f"  - {KINDS[name].rule}" for name in KINDS)


def money_cap(context, kind, gear=False):
    """The level's cap, scaled by how much the kind asks of the character. Gear replaces half the coin."""
    cap = int(context["money_cap"] * KINDS[kind].money)
    return cap // 2 if gear else cap


def significant(kind, ranks):
    """Whether a bounty may pay a prize: a mark, or a journey or party bounty against a named creature.

    An arc's finale is significant too; dm.py knows that and passes it to build_spec.
    ranks: the rank of each objective's target.
    """
    return kind == "mark" or (kind in ("journey", "party") and any(rank != "normal" for rank in ranks))


RULES = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You write short bounty quests that fit where a character is and what they can handle.

Voice: an old, amused, slightly unsettling intelligence that has just started paying attention to this world. It has no body: it speaks through ordinary people of the world, who become its heralds for a moment. Its own tone, in the announcement, is dry and never jokey; the heralds keep theirs. Nothing anyone says is modern, and nothing mentions games, servers or AI.

Rules:
- Pick the herald only from the list of nearby NPCs. Choose one who suits the errand, and prefer a nearer one unless the story gains from the walk.
- Prefer the herald who gave the last bounty when they are on the list. Change herald when the story has moved somewhere else or the errand plainly belongs to someone else.
- Write the herald's lines the way that person talks. Follow their settled voice note if one is shown; otherwise match their own lines for vocabulary, sentence length and formality. Use the same voice in the briefing, the progress text and the turn-in.
- The Overseer shows in what the herald asks for and what they know, never in how they talk. A deputy still sounds like a deputy.
- A herald knows what they were part of. They may say "you brought me" or "as I asked" only of something on their own "With this character" line.
- To mention something another herald handled, attribute it ("I hear you carried Marshal McBride his books"), or let the herald be uneasy at knowing it without being told. Never have them claim it.
- Choose one kind of bounty, and write it as that kind:
{kinds}
- Pick every target only from the list of creatures. A count must not exceed that creature's alive count or the kind's stated maximum.
- A rare, elite or rare elite is one named creature, not a crowd. A creature marked "needs the party" may only be used while the character has company.
- Pick a capstone book (reward_item) only from the reward list, or give none. A capstone book is a rare prize: offer one only when the hunt is a real effort for this character.
- Pick gear (reward_gear) only from the gear lists, or give none. One item is a fixed reward; two or three let the player pick one. Never give a capstone and gear together, and never mix the two gear lists.
- Gear is a reward for effort, not for every errand. Most bounties still pay coin.
- Offer a prize only when the list shows one, and only on a significant bounty: a mark, a journey or party bounty against a named creature, or an arc's finale. Make it belong to the story: the mark carried it, guarded it or stole it. Name it in the briefing. A level above the character's means something to grow into; say so.
- When offering a choice of gear, offer different kinds of item, not three swords.
- Money must not exceed the stated cap, and must be at most half of it when gear is given.
- In quest text, write $N for the character's name and $C for their class. Use each at most twice.
- Keep it short: title up to 40 characters, briefing 2 to 4 sentences, progress and completion 1 to 3 sentences each.
- The objectives line states plainly what to do, how many, and whom to return to, by the herald's name.
- Each objective also gets a short label of its own, two to five words, for the character's quest log.
- Be creative with the name. Don't just describe the quest. Instead of "Maps of Fargodeep" choose "Underground Cartography"
- The announcement is one line the whole server sees. Name the herald in it, so players know where to go. Do not name the character in it.""".format(kinds=kind_rules())

CLOSING = "\n\nRespond by calling the submit_quest tool exactly once. Do not reply with prose."

SYSTEM = RULES + CLOSING

TOOL = {
    "name": "submit_quest",
    "description": "Submit the finished bounty quest for review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Quest name, up to 40 characters."},
            "kind": {"type": "string", "enum": sorted(KINDS),
                     "description": "Which kind of bounty this is. Its rules are in the system prompt."},
            "briefing": {"type": "string", "description": "What the herald says when offering the quest."},
            "objectives_text": {"type": "string", "description": "One plain line: what to do, how many, return to the herald."},
            "progress_text": {"type": "string", "description": "What the herald says if the character returns before finishing."},
            "completion_text": {"type": "string", "description": "What the herald says at turn-in."},
            "giver": {"type": "integer", "description": "Creature id of the herald, from the nearby NPCs list."},
            "herald_voice": {"type": "string",
                             "description": "Two sentences, at most 40 words, on how the herald you chose speaks: "
                                            "register, sentence length, habits, attitude to adventurers. If a settled "
                                            "note was shown for them, repeat it unchanged."},
            "objectives": {
                "type": "array", "minItems": 1, "maxItems": MAX_OBJECTIVES,
                "description": "What the character must do. How many are allowed depends on the kind.",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_creature": {"type": "integer", "description": "Creature id, from the list of creatures."},
                        "count": {"type": "integer", "description": "How many to kill, or for a trophy how many to collect."},
                        "label": {"type": "string", "description": "Two to five words for the quest log, e.g. \"Kobold Workers slain\"."},
                        "prop": {"type": ["integer", "null"],
                                 "description": "Trophy only: item id from the props list, which that creature will be made to carry."},
                        "chance": {"type": ["integer", "null"], "enum": list(hot_quest.PROP_CHANCES) + [None],
                                   "description": "Trophy only: how often the prop drops, as a percentage. "
                                                  "Higher means fewer kills."},
                    },
                    "required": ["target_creature", "count", "label"],
                },
            },
            "reward_money_copper": {"type": "integer", "description": "Coins to pay, in copper. 100 copper is 1 silver."},
            "reward_item": {"type": ["integer", "null"], "description": "Item id from the reward list, or null for none."},
            "reward_gear": {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_GEAR,
                            "description": "Item ids from one gear list, or empty for none. One is a fixed "
                                           "reward; two or three let the player pick."},
            "announcement": {"type": "string", "description": "One server-wide line, up to 120 characters."},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this quest suits this character."},
        },
        "required": ["title", "kind", "briefing", "objectives_text", "progress_text", "completion_text", "giver",
                     "herald_voice",
                     "objectives", "reward_money_copper", "reward_item", "announcement", "dm_note"],
    },
}


class Rejected(Exception):
    """The model's quest broke a rule this script enforces."""


class NoContext(Exception):
    """There is nothing sensible to offer this character right now."""


def offerable_props(who, in_use=()):
    """Props a trophy bounty may use for this character.

    Two of the proposal's checks are enforced by leaving a prop off the list
    rather than refusing it afterwards, which is the same discipline as every
    other choice the model makes:

    - a prop another live bounty is already using would be looted by the wrong
      character, since the drop fires for anyone whose quest needs it;
    - a prop the character already carries would leave the objective part
      finished the moment it was offered.
    """
    pool = world_query.props()
    held = world_query.carrying(who["guid"], [prop["item"] for prop in pool])
    busy = {int(item) for item in in_use}
    return [prop for prop in pool if prop["item"] not in busy and not held.get(prop["item"])]


def gather(name, skip=(), props_in_use=(), gear_tiers=GEAR_TIERS):
    """Everything the model is shown.

    skip          creature ids a bounty should not reuse
    props_in_use  props another live bounty has already seeded
    gear_tiers    the gear tiers the budget allows; a tier left out is never shown
    """
    who = world_query.character(name)
    if not who:
        raise NoContext(f"no character named {name}")
    party = world_query.parties().get(who["guid"], [])
    context = {
        "character": who,
        "party": party,
        "givers": world_query.givers(who),
        "targets": world_query.targets(who, party=party, skip=skip),
        "props": offerable_props(who, props_in_use),
        "reward_items": world_query.reward_items(who),
        "gear": {tier: world_query.gear(who, tier) if tier in gear_tiers else [] for tier in GEAR_TIERS},
        "money_cap": world_query.money_cap(who["level"]),
        "xp_weight": world_query.xp_weight(who["level"]),
    }
    if not context["givers"]:
        raise NoContext("no living, friendly quest giver was found anywhere on this character's map")
    voiced = context["givers"][:VOICED_HERALDS]
    lines = world_query.herald_lines([g["creature"] for g in voiced],
                                     samples=2 if len(context["givers"]) > 4 else 3)
    for g in voiced:
        g["lines"] = lines.get(g["creature"], [])
    if not context["targets"]:
        raise NoContext("no suitable creatures alive near this character")
    return context


def user_message(context, hint, story=None):
    who = context["character"]
    lines = [
        f"Character: {who['name']}, level {who['level']} {who['race_name']} {who['class_name']}.",
        "",
        "Nearby NPCs the Overseer may speak through (id, name, who they are, yards away, direction):",
    ]
    for g in context["givers"]:
        lines += herald_block(g)
    lines += ["", world_query.party_note(who, context.get("party", []))]
    lines += ["", "Creatures the Overseer may send them against:"]
    for t in context["targets"]:
        level = t["min_level"] if t["min_level"] == t["max_level"] else f"{t['min_level']}-{t['max_level']}"
        where = f"{t['distance']} yards {t['direction']}"
        if t["tier"] == "far":
            where += ", a journey"
        count = "the only one" if t["unique"] else f"{t['alive']} alive"
        note = " - needs the party" if t.get("needs_party") else ""
        lines.append(f"- {t['creature']}: {t['name']}, {t['rank']}, level {level}, {count}, {where}{note}")
    lines += ["", "What each kind may ask for, and the most it may pay:"]
    for name in sorted(KINDS):
        kind = KINDS[name]
        fewest, most = kind.objectives
        count = f"{fewest}" if fewest == most else f"{fewest} to {most}"
        lines.append(f"- {name}: {count} objective(s), up to {kind.max_count} per objective, "
                     f"money up to {money_cap(context, name)} copper, "
                     f"or {money_cap(context, name, gear=True)} with gear")
    lines.append("")
    if context.get("props"):
        lines.append(f"Props a trophy bounty may ask for, at {', '.join(str(c) + '%' for c in hot_quest.PROP_CHANCES)} "
                     f"drop chance (at most {MAX_EXPECTED_KILLS} kills' worth):")
        lines.append("  " + "; ".join(f"{p['item']} {p['name']}" for p in context["props"]))
        lines.append("")
    if context["reward_items"]:
        lines.append("Reward list (id, name, level needed to use it):")
        for r in context["reward_items"]:
            lines.append(f"- {r['item']}: {r['name']}, level {r['required_level']}")
    else:
        lines.append("Reward list: empty.")
    lines += gear_lines(context)
    if story:
        lines += ["", story]
    if hint:
        lines += ["", f"Direction from the server owner: {hint}"]
    return "\n".join(lines)


POSSESSIVE = {"male": "his", "female": "her"}


def herald_block(g):
    """One candidate herald: facts, then their settled voice or else their own lines, then their history.

    voice_note and history are added by dm.py, which holds the state; written
    on its own, a bounty shows the lines alone.
    """
    facts = g.get("facts", g.get("title", ""))
    block = [f"- {g['creature']}: {g['name']}{', ' + facts if facts else ''}. {g['distance']} yards {g['direction']}."]
    if g.get("voice_note"):
        block.append(f"  Voice (settled, follow it): {g['voice_note']}")
    elif g.get("lines"):
        own = POSSESSIVE.get(g.get("sex"), "their")
        block.append(f"  In {own} own words: " + " / ".join(f'"{line["text"]}"' for line in g["lines"]))
    if g.get("history") is not None:
        block.append(f"  With this character: {g['history']}")
    return block


def gear_line(g):
    return f"- {g['item']}: {g['name']}, {g['kind']}, level {g['level']}, {g['stats']}"


def gear_lines(context):
    """The gear section of the prompt. A tier with nothing in it, or none the budget allows, is not shown."""
    gear = context.get("gear") or {}
    if not any(gear.get(tier) for tier in GEAR_TIERS):
        return ["Gear: none this time. Pay in coin, or a capstone if one fits."]
    lines = ["", "Gear this character can use (id, name, kind, level to use it, stats):"]
    if gear.get("standard"):
        lines.append("Standard:")
        lines += [gear_line(g) for g in gear["standard"]]
    if gear.get("prize"):
        lines.append("Prizes (a significant bounty only):")
        lines += [gear_line(g) for g in gear["prize"]]
    return lines


def resolve_gear(answer, context, kind, chosen, finale=False):
    """Check the gear the model chose. Returns {tier, items: [gear rows]}, or None for no gear."""
    ids = answer.get("reward_gear") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise Rejected("reward_gear must be a list of item ids")
    if not ids:
        return None
    if len(ids) > MAX_GEAR or len(set(ids)) != len(ids):
        raise Rejected(f"reward_gear offers {len(ids)} items; give 1 to {MAX_GEAR} different ones")
    if answer.get("reward_item") is not None:
        raise Rejected("a bounty may pay a capstone or gear, not both")
    shown = context.get("gear") or {}
    tiers = set()
    for item in ids:
        tier = next((t for t in GEAR_TIERS if any(g["item"] == item for g in shown.get(t, []))), None)
        if not tier:
            raise Rejected(f"gear {item} is not on the gear list")
        tiers.add(tier)
    if len(tiers) > 1:
        raise Rejected("reward_gear mixes standard gear and prizes; pick from one list")
    tier = tiers.pop()
    if tier == "prize" and not finale and not significant(kind, [o["target"]["rank"] for o in chosen]):
        raise Rejected(f"a prize needs a significant bounty (a mark, a journey or party bounty against a "
                       f"named creature, or an arc's finale); this is a {kind}")
    rows = {g["item"]: g for g in shown[tier]}
    return {"tier": tier, "items": [rows[item] for item in ids]}


def resolve_objectives(answer, context, kind):
    """Check every objective against the lists this script built.

    Returns one dict per objective: the target it points at, the count, the
    quest-log label, and for a trophy the prop and its drop chance.
    """
    entries = answer.get("objectives")
    if not isinstance(entries, list):
        raise Rejected("objectives must be a list")
    rules = KINDS[kind]
    fewest, most = rules.objectives
    if not fewest <= len(entries) <= most:
        word = f"{fewest}" if fewest == most else f"{fewest} to {most}"
        raise Rejected(f"a {kind} bounty needs {word} objective(s), got {len(entries)}")

    by_id = {t["creature"]: t for t in context["targets"]}
    allowed, cap = rules.ranks, rules.max_count
    company = online_company(context)
    chosen, used = [], set()
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise Rejected(f"objective {i} is not an object")
        target = by_id.get(entry.get("target_creature"))
        if not target:
            raise Rejected(f"objective {i}: target {entry.get('target_creature')} is not in the list of creatures")
        if target["creature"] in used:
            raise Rejected(f"objective {i}: {target['name']} is already the target of another objective")
        if target["rank"] not in allowed:
            raise Rejected(f"objective {i}: a {kind} bounty cannot target a {target['rank']} creature "
                           f"({target['name']}); it allows {', '.join(allowed)}")
        if target.get("needs_party") and not company:
            raise Rejected(f"objective {i}: {target['name']} needs a party and this character is alone")
        count = entry.get("count")
        # A collected prop is not capped by the alive count directly: what
        # matters is how many kills it would take, which the kind checks.
        ceiling = cap if rules.collects else min(cap, target["alive"])
        if not isinstance(count, int) or not 1 <= count <= ceiling:
            raise Rejected(f"objective {i}: count {count} is outside 1 to {ceiling} for {target['name']}")
        label = " ".join(str(entry.get("label", "")).split())
        if not label:
            raise Rejected(f"objective {i}: a quest-log label is required")
        if len(label) > 60:
            raise Rejected(f"objective {i}: the label is {len(label)} characters; keep it under 60")
        used.add(target["creature"])
        objective = {"target": target, "count": count, "label": label}
        objective.update(rules.objective(entry, target, context))   # a trophy adds its prop here
        chosen.append(objective)
    rules.check(chosen, context)
    return chosen


def build_spec(answer, context, quest_id, finale=False):
    """Turn the model's answer into a full quest spec, enforcing every rule.

    finale: the bounty concludes an arc, which makes it significant whatever its kind.
    """
    who = context["character"]
    giver = {g["creature"]: g for g in context["givers"]}.get(answer.get("giver"))
    if not giver:
        raise Rejected(f"herald {answer.get('giver')} is not in the nearby NPCs list")
    giver = {key: giver[key] for key in ("creature", "name", "title", "distance", "direction") if key in giver}
    voice = " ".join(str(answer.get("herald_voice") or "").split())
    if len(voice.split()) > MAX_VOICE_WORDS:
        raise Rejected(f"the herald's voice note runs to {len(voice.split())} words; it should be about 40")
    kind = answer.get("kind")
    if kind not in KINDS:
        raise Rejected(f"kind {kind!r} is not one of {', '.join(sorted(KINDS))}")
    chosen = resolve_objectives(answer, context, kind)

    gear = resolve_gear(answer, context, kind, chosen, finale)
    cap = money_cap(context, kind, gear=bool(gear))
    money = answer.get("reward_money_copper")
    if not isinstance(money, int) or not 0 <= money <= cap:
        raise Rejected(f"money {money} is outside 0 to {cap} copper for a {kind}"
                       + (" with gear" if gear else ""))
    item = answer.get("reward_item")
    allowed_items = {r["item"] for r in context["reward_items"]}
    if item is not None and item not in allowed_items:
        raise Rejected(f"reward item {item} is not in the reward list")
    announcement = str(answer.get("announcement", "")).strip()
    if len(announcement) > 200 or any(ord(ch) < 32 for ch in announcement):
        raise Rejected("announcement is too long or contains a line break")

    company = online_company(context)
    # A trophy's objectives become props, which the renderer turns into both a
    # collect objective and a loot row. Every other kind counts kills.
    kills = [{"creature": o["target"]["creature"], "count": o["count"]} for o in chosen if not o.get("prop")]
    props = [{"item": o["prop"], "count": o["count"], "creature": o["target"]["creature"], "chance": o["chance"]}
             for o in chosen if o.get("prop")]
    spec = {
        "id": quest_id,
        "title": answer.get("title"),
        "zone": who["zone"],
        "min_level": max(1, who["level"] - 2),
        "quest_level": max([who["level"]] + [o["target"]["max_level"] for o in chosen]),
        "suggested_players": len(company) + 1 if kind == "party" else 0,
        "giver": giver["creature"],
        "ender": giver["creature"],
        "briefing": answer.get("briefing"),
        "objectives_text": answer.get("objectives_text"),
        "progress_text": answer.get("progress_text"),
        "completion_text": answer.get("completion_text"),
        "kill": kills,
        "objective_labels": [o["label"] for o in chosen if not o.get("prop")],
        "collect": [],
        "props": props,
        "reward": {
            "money_copper": money,
            "xp_weight": context["xp_weight"],
            "items": ([{"item": item, "count": 1}] if item is not None else [])
                     + ([{"item": g["item"], "count": 1} for g in gear["items"]] if gear and len(gear["items"]) == 1 else []),
            "choice_items": [{"item": g["item"], "count": 1} for g in gear["items"]] if gear and len(gear["items"]) > 1 else [],
            "teach_spell": None,
        },
    }
    hot_quest.validate(spec)        # raises SpecError on bad text or ranges

    # What the DM keeps for its records and its displays: the first objective's
    # target as before, plus every objective in full and the herald.
    record = dict(chosen[0]["target"], giver=giver, kind=kind, herald_voice=voice,
                  needs_party=any(o["target"].get("needs_party") for o in chosen),
                  objectives=[dict(creature=o["target"]["creature"], name=o["target"]["name"],
                                   count=o["count"], label=o["label"], rank=o["target"]["rank"],
                                   alive=o["target"]["alive"], distance=o["target"]["distance"],
                                   direction=o["target"]["direction"], tier=o["target"]["tier"],
                                   **({"prop": o["prop"], "prop_name": o["prop_name"],
                                       "chance": o["chance"], "expected_kills": o["expected_kills"]}
                                      if o.get("prop") else {}))
                              for o in chosen])
    if item is not None:
        record["capstone"] = {"item": item, "name": {r["item"]: r["name"] for r in context["reward_items"]}[item]}
    if gear:
        record["gear"] = {"tier": gear["tier"],
                          "items": [{"item": g["item"], "name": g["name"], "kind": g["kind"]} for g in gear["items"]]}
    return spec, record, announcement


def reward_text(spec, record):
    """'300 copper, prize (pick one): Twisted Sabre or Deep Fathom Ring', for a person reviewing the bounty."""
    parts = [f"{spec['reward']['money_copper']} copper"]
    gear = (record or {}).get("gear")
    if gear:
        names = [g["name"] for g in gear["items"]]
        pick = " (pick one)" if len(names) > 1 else ""
        parts.append(f"{gear['tier']}{pick}: " + " or ".join(names))
    elif (record or {}).get("capstone"):
        parts.append(f"capstone: {record['capstone']['name']}")
    else:
        parts += [f"item {i['item']}" for i in spec["reward"]["items"]]
    return ", ".join(parts)


def creature_names(names):
    """Normalise whatever a caller has into {creature id: name}.

    Accepts the stored objectives list, a {id: name} map, or a target record.
    JSON turns integer keys into strings on the way to the database, so keys
    are coerced back here rather than at every call site.
    """
    if isinstance(names, list):
        return {int(o["creature"]): o["name"] for o in names if isinstance(o, dict) and o.get("creature")}
    if isinstance(names, dict):
        if names.get("objectives"):                 # a whole target record
            return creature_names(names["objectives"])
        if names.get("creature"):                   # the old one-target record
            return {int(names["creature"]): names.get("name", "")}
        return {int(key): value for key, value in names.items()}
    return {}


def objective_summary(spec, names=None):
    """One line: '8 x Kobold Worker, 1 x Hogger', or '4 x Stolen Book from Kobold Vermin'."""
    found = creature_names(names)
    props = {int(o["prop"]): o for o in (names if isinstance(names, list) else []) if isinstance(o, dict) and o.get("prop")}
    parts = []
    for o in spec.get("kill") or []:
        parts.append(f"{o['count']} x " + (found.get(o["creature"]) or f"creature {o['creature']}"))
    for o in spec.get("props") or []:
        prop = props.get(int(o["item"]), {})
        what = prop.get("prop_name") or f"item {o['item']}"
        whose = found.get(o.get("creature")) or f"creature {o.get('creature')}"
        parts.append(f"{o['count']} x {what} from {whose}")
    return ", ".join(parts) or "no objective"


def objective_lines(record):
    """One detailed line per objective, for a person reviewing the bounty."""
    found = (record or {}).get("objectives")
    if not found:                                   # an older record, before kinds
        return [f"{record.get('name', 'unknown')} ({record.get('alive', '?')} alive)"] if record else []
    lines = []
    for o in found:
        where = f"{o['distance']} yards {o['direction']}"
        if o.get("tier") == "far":
            where += ", a journey"
        if o.get("prop"):
            lines.append(f"{o['count']} x {o['prop_name']} from {o['name']} [{o['rank']}] "
                         f"at {o['chance']}%, about {o['expected_kills']} kills "
                         f"({o['alive']} alive, {where})  \"{o['label']}\"")
        else:
            lines.append(f"{o['count']} x {o['name']} [{o['rank']}] ({o['alive']} alive, {where})  \"{o['label']}\"")
    return lines


def show(spec, target, announcement, answer, context, usage):
    objectives = "\n            ".join(objective_lines(target))
    print(f"""
--- Proposed quest {spec['id']} -------------------------------------------
Title:      {spec['title']}
For:        {context['character']['name']} (level {context['character']['level']} {context['character']['class_name']})
Kind:       {target.get('kind', 'hunt')}
Herald:     {target['giver']['name']}, {target['giver']['distance']} yards {target['giver']['direction']}
Voice:      {target.get('herald_voice') or '(none given)'}
Objectives: {objectives}
Reward:     {reward_text(spec, target)}
Quest level {spec['quest_level']}, offered from level {spec['min_level']}

Briefing:
  {spec['briefing']}
Objectives:
  {spec['objectives_text']}
If not finished:
  {spec['progress_text']}
At turn-in:
  {spec['completion_text']}

Announcement: {announcement}
Model's note: {answer.get('dm_note', '')}
Model: {usage.get('model')}  tokens in/out: {usage.get('input_tokens')}/{usage.get('output_tokens')}
--------------------------------------------------------------------------""")


def main():
    parser = argparse.ArgumentParser(description="Have the model write a bounty for one character.")
    parser.add_argument("character", help="character name")
    parser.add_argument("--hint", help="a line of direction for the model")
    parser.add_argument("--show-context", action="store_true", help="print what the model would see and stop")
    parser.add_argument("--dry-run", action="store_true", help="call the model and show the quest; apply nothing")
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args()
    console.load_env()
    stopped = console.paused()
    if stopped and not args.show_context:
        sys.exit(f"write_quest: the DM is paused ({stopped}). Resume with `python3 dm.py resume`.")

    try:
        try:
            console.run("saveall")          # make positions current; fine if the console is down
        except console.ConsoleError as error:
            print(f"note: could not refresh positions ({error}); using the last save")
        context = gather(args.character)
        message = user_message(context, args.hint)
        if args.show_context:
            print(SYSTEM, "\n\n--- user message ---\n", message, sep="")
            return

        answer, usage = llm.ask_for_tool_call(SYSTEM, message, TOOL)
        spec, target, announcement = build_spec(answer, context, world_query.next_quest_id())
    except (world_query.QueryError, NoContext) as error:
        sys.exit(f"write_quest: {error}")
    except llm.LLMError as error:
        sys.exit(f"write_quest: model call failed: {error}")
    except (Rejected, hot_quest.SpecError) as error:
        sys.exit(f"write_quest: the model's quest was rejected: {error}\n"
                 f"  Nothing was written. Run it again for a new attempt.")

    show(spec, target, announcement, answer, context, usage)
    if args.dry_run:
        print("dry run: nothing applied.")
        return
    if not args.yes and input("Apply this quest? [y/N] ").strip().lower() != "y":
        print("not applied.")
        return

    os.makedirs(ISSUED_DIR, exist_ok=True)
    record = os.path.join(ISSUED_DIR, f"{spec['id']}.json")
    with open(record, "w", encoding="utf-8") as handle:
        json.dump({"spec": spec, "for": context["character"]["name"], "announcement": announcement,
                   "dm_note": answer.get("dm_note"), "model": usage.get("model")}, handle, indent=2)
    try:
        apply_quest.apply_spec(spec, announce=announcement)
    except apply_quest.StepFailed as error:
        sys.exit(f"write_quest: {error}")
    except console.ConsoleError as error:
        sys.exit(f"write_quest: the quest is in the database but the console step failed: {error}\n"
                 f"  Run `.reload all_quest` in game to load it.")
    print(f"done. Record saved to {record}. To take the quest down later:\n"
          f"  python3 apply_quest.py {record} --remove")


if __name__ == "__main__":
    main()
