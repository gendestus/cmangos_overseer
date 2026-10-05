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
import os
import sys

import apply_quest
import console
import hot_quest
import llm
import world_query

ISSUED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issued")

MAX_OBJECTIVES = 2          # the quest format allows four; two is enough to start


class Kind:
    """One sort of bounty: what it may ask for, and what it is worth.

    objectives   how many objectives the kind allows, as (fewest, most)
    ranks        creature ranks it may target, from world_query.RANK_NAMES
    max_count    ceiling on one objective's count, before the alive count is applied
    money        multiplier on the level's money cap, so harder work pays better
    rule         the sentence the model is told about this kind
    """

    def __init__(self, objectives, ranks, max_count, money, rule):
        self.objectives, self.ranks = objectives, ranks
        self.max_count, self.money, self.rule = max_count, money, rule

    def check(self, chosen, context):
        """Rules that need the whole answer, not one objective. Raises Rejected."""


class Journey(Kind):
    def check(self, chosen, context):
        if not any(target["tier"] == "far" for _, target, _ in chosen):
            raise Rejected("a journey must send the character to a far target; all of these are nearby")


class Party(Kind):
    def check(self, chosen, context):
        if not online_company(context):
            raise Rejected("a party bounty needs company; this character is alone")


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
}


def online_company(context):
    return [other for other in context.get("party", []) if other.get("online")]


def kind_rules():
    """The kinds, as lines for the prompt."""
    return "\n".join(f"  - {KINDS[name].rule}" for name in KINDS)


def money_cap(context, kind):
    """The level's cap, scaled by how much the kind asks of the character."""
    return int(context["money_cap"] * KINDS[kind].money)


RULES = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You write short bounty quests that fit where a character is and what they can handle.

Voice: an old, amused, slightly unsettling intelligence that has just started paying attention to this world. It has no body: it speaks through ordinary people of the world, who become its heralds for a moment. Dry, never jokey, never modern, no mention of games, servers or AI.

Rules:
- Pick the herald only from the list of nearby NPCs. Choose one who suits the errand, and prefer a nearer one unless the story gains from the walk. Write their lines as that person would speak, with something else behind the words; a guard captain, an innkeeper and a priestess should not sound alike.
- Choose one kind of bounty, and write it as that kind:
{kinds}
- Pick every target only from the list of creatures. A count must not exceed that creature's alive count or the kind's stated maximum.
- A rare, elite or rare elite is one named creature, not a crowd. A creature marked "needs the party" may only be used while the character has company.
- Pick the item reward only from the reward list, or give none. A capstone book is a rare prize: offer one only when the hunt is a real effort for this character.
- Money must not exceed the stated cap.
- In quest text, write $N for the character's name and $C for their class. Use each at most twice.
- Keep it short: title up to 40 characters, briefing 2 to 4 sentences, progress and completion 1 to 3 sentences each.
- The objectives line states plainly what to do, how many, and whom to return to, by the herald's name.
- Each objective also gets a short label of its own, two to five words, for the character's quest log.
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
            "objectives": {
                "type": "array", "minItems": 1, "maxItems": MAX_OBJECTIVES,
                "description": "What the character must do. How many are allowed depends on the kind.",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_creature": {"type": "integer", "description": "Creature id, from the list of creatures."},
                        "count": {"type": "integer", "description": "How many to kill."},
                        "label": {"type": "string", "description": "Two to five words for the quest log, e.g. \"Kobold Workers slain\"."},
                    },
                    "required": ["target_creature", "count", "label"],
                },
            },
            "reward_money_copper": {"type": "integer", "description": "Coins to pay, in copper. 100 copper is 1 silver."},
            "reward_item": {"type": ["integer", "null"], "description": "Item id from the reward list, or null for none."},
            "announcement": {"type": "string", "description": "One server-wide line, up to 120 characters."},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this quest suits this character."},
        },
        "required": ["title", "kind", "briefing", "objectives_text", "progress_text", "completion_text", "giver",
                     "objectives", "reward_money_copper", "reward_item", "announcement", "dm_note"],
    },
}


class Rejected(Exception):
    """The model's quest broke a rule this script enforces."""


class NoContext(Exception):
    """There is nothing sensible to offer this character right now."""


def gather(name, skip=()):
    """Everything the model is shown. `skip` is creature ids a bounty should not reuse."""
    who = world_query.character(name)
    if not who:
        raise NoContext(f"no character named {name}")
    party = world_query.parties().get(who["guid"], [])
    context = {
        "character": who,
        "party": party,
        "givers": world_query.givers(who),
        "targets": world_query.targets(who, party=party, skip=skip),
        "reward_items": world_query.reward_items(who),
        "money_cap": world_query.money_cap(who["level"]),
        "xp_weight": world_query.xp_weight(who["level"]),
    }
    if not context["givers"]:
        raise NoContext("no living, friendly quest giver was found anywhere on this character's map")
    if not context["targets"]:
        raise NoContext("no suitable creatures alive near this character")
    return context


def user_message(context, hint, story=None):
    who = context["character"]
    lines = [
        f"Character: {who['name']}, level {who['level']} {who['race_name']} {who['class_name']}.",
        "",
        "Nearby NPCs the Overseer may speak through (id, name, role, yards away, direction):",
    ]
    for g in context["givers"]:
        role = f", {g['title']}" if g["title"] else ""
        lines.append(f"- {g['creature']}: {g['name']}{role}, {g['distance']} yards {g['direction']}")
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
                     f"money up to {money_cap(context, name)} copper")
    lines.append("")
    if context["reward_items"]:
        lines.append("Reward list (id, name, level needed to use it):")
        for r in context["reward_items"]:
            lines.append(f"- {r['item']}: {r['name']}, level {r['required_level']}")
    else:
        lines.append("Reward list: empty. Pay in money only.")
    if story:
        lines += ["", story]
    if hint:
        lines += ["", f"Direction from the server owner: {hint}"]
    return "\n".join(lines)


def resolve_objectives(answer, context, kind):
    """Check every objective against the lists this script built. Returns a list of
    (spec objective, the target it points at, quest-log label)."""
    entries = answer.get("objectives")
    if not isinstance(entries, list):
        raise Rejected("objectives must be a list")
    fewest, most = KINDS[kind].objectives
    if not fewest <= len(entries) <= most:
        word = f"{fewest}" if fewest == most else f"{fewest} to {most}"
        raise Rejected(f"a {kind} bounty needs {word} objective(s), got {len(entries)}")

    by_id = {t["creature"]: t for t in context["targets"]}
    allowed, cap = KINDS[kind].ranks, KINDS[kind].max_count
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
        ceiling = min(cap, target["alive"])
        count = entry.get("count")
        if not isinstance(count, int) or not 1 <= count <= ceiling:
            raise Rejected(f"objective {i}: count {count} is outside 1 to {ceiling} for {target['name']}")
        label = " ".join(str(entry.get("label", "")).split())
        if not label:
            raise Rejected(f"objective {i}: a quest-log label is required")
        if len(label) > 60:
            raise Rejected(f"objective {i}: the label is {len(label)} characters; keep it under 60")
        used.add(target["creature"])
        chosen.append(({"creature": target["creature"], "count": count}, target, label))
    KINDS[kind].check(chosen, context)
    return chosen


def build_spec(answer, context, quest_id):
    """Turn the model's answer into a full quest spec, enforcing every rule."""
    who = context["character"]
    giver = {g["creature"]: g for g in context["givers"]}.get(answer.get("giver"))
    if not giver:
        raise Rejected(f"herald {answer.get('giver')} is not in the nearby NPCs list")
    kind = answer.get("kind")
    if kind not in KINDS:
        raise Rejected(f"kind {kind!r} is not one of {', '.join(sorted(KINDS))}")
    chosen = resolve_objectives(answer, context, kind)

    cap = money_cap(context, kind)
    money = answer.get("reward_money_copper")
    if not isinstance(money, int) or not 0 <= money <= cap:
        raise Rejected(f"money {money} is outside 0 to {cap} copper for a {kind}")
    item = answer.get("reward_item")
    allowed_items = {r["item"] for r in context["reward_items"]}
    if item is not None and item not in allowed_items:
        raise Rejected(f"reward item {item} is not in the reward list")
    announcement = str(answer.get("announcement", "")).strip()
    if len(announcement) > 200 or any(ord(ch) < 32 for ch in announcement):
        raise Rejected("announcement is too long or contains a line break")

    company = online_company(context)
    spec = {
        "id": quest_id,
        "title": answer.get("title"),
        "zone": who["zone"],
        "min_level": max(1, who["level"] - 2),
        "quest_level": max([who["level"]] + [t["max_level"] for _, t, _ in chosen]),
        "suggested_players": len(company) + 1 if kind == "party" else 0,
        "giver": giver["creature"],
        "ender": giver["creature"],
        "briefing": answer.get("briefing"),
        "objectives_text": answer.get("objectives_text"),
        "progress_text": answer.get("progress_text"),
        "completion_text": answer.get("completion_text"),
        "kill": [objective for objective, _, _ in chosen],
        "objective_labels": [label for _, _, label in chosen],
        "collect": [],
        "reward": {
            "money_copper": money,
            "xp_weight": context["xp_weight"],
            "items": [{"item": item, "count": 1}] if item is not None else [],
            "choice_items": [],
            "teach_spell": None,
        },
    }
    hot_quest.validate(spec)        # raises SpecError on bad text or ranges

    # What the DM keeps for its records and its displays: the first objective's
    # target as before, plus every objective in full and the herald.
    primary = chosen[0][1]
    record = dict(primary, giver=giver, kind=kind, needs_party=any(t.get("needs_party") for _, t, _ in chosen),
                  objectives=[dict(creature=t["creature"], name=t["name"], count=o["count"], label=label,
                                   rank=t["rank"], alive=t["alive"], distance=t["distance"],
                                   direction=t["direction"], tier=t["tier"])
                              for o, t, label in chosen])
    return spec, record, announcement


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
    """One line: '8 x Kobold Worker, 1 x Hogger'."""
    found = creature_names(names)
    parts = []
    for o in spec.get("kill") or []:
        parts.append(f"{o['count']} x " + (found.get(o["creature"]) or f"creature {o['creature']}"))
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
        lines.append(f"{o['count']} x {o['name']} [{o['rank']}] ({o['alive']} alive, {where})  \"{o['label']}\"")
    return lines


def show(spec, target, announcement, answer, context, usage):
    item_names = {r["item"]: r["name"] for r in context["reward_items"]}
    reward = [f"{spec['reward']['money_copper']} copper"]
    reward += [item_names[i["item"]] for i in spec["reward"]["items"]]
    objectives = "\n            ".join(objective_lines(target))
    print(f"""
--- Proposed quest {spec['id']} -------------------------------------------
Title:      {spec['title']}
For:        {context['character']['name']} (level {context['character']['level']} {context['character']['class_name']})
Kind:       {target.get('kind', 'hunt')}
Herald:     {target['giver']['name']}, {target['giver']['distance']} yards {target['giver']['direction']}
Objectives: {objectives}
Reward:     {', '.join(reward)}
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
