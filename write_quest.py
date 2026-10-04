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

MAX_KILLS = 12
ISSUED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "issued")

RULES = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You write short bounty quests that fit where a character is and what they can handle.

Voice: an old, amused, slightly unsettling intelligence that has just started paying attention to this world. It has no body: it speaks through ordinary people of the world, who become its heralds for a moment. Dry, never jokey, never modern, no mention of games, servers or AI.

Rules:
- Pick the herald only from the list of nearby NPCs. Choose one who suits the errand, and prefer a nearer one unless the story gains from the walk. Write their lines as that person would speak, with something else behind the words; a guard captain, an innkeeper and a priestess should not sound alike.
- Pick the hunt target only from the list of nearby creatures. The kill count must not exceed that creature's alive count or the stated maximum.
- Pick the item reward only from the reward list, or give none. A capstone book is a rare prize: offer one only when the hunt is a real effort for this character.
- Money must not exceed the stated cap.
- In quest text, write $N for the character's name and $C for their class. Use each at most twice.
- Keep it short: title up to 40 characters, briefing 2 to 4 sentences, progress and completion 1 to 3 sentences each.
- The objectives line states plainly what to kill, how many, and whom to return to, by the herald's name.
- The announcement is one line the whole server sees. Name the herald in it, so players know where to go. Do not name the character in it."""

CLOSING = "\n\nRespond by calling the submit_quest tool exactly once. Do not reply with prose."

SYSTEM = RULES + CLOSING

TOOL = {
    "name": "submit_quest",
    "description": "Submit the finished bounty quest for review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Quest name, up to 40 characters."},
            "briefing": {"type": "string", "description": "What the herald says when offering the quest."},
            "objectives_text": {"type": "string", "description": "One plain line: what to kill, how many, return to the herald."},
            "progress_text": {"type": "string", "description": "What the herald says if the character returns before finishing."},
            "completion_text": {"type": "string", "description": "What the herald says at turn-in."},
            "giver": {"type": "integer", "description": "Creature id of the herald, from the nearby NPCs list."},
            "target_creature": {"type": "integer", "description": "Creature id, from the nearby creatures list."},
            "kill_count": {"type": "integer", "description": "How many to kill."},
            "reward_money_copper": {"type": "integer", "description": "Coins to pay, in copper. 100 copper is 1 silver."},
            "reward_item": {"type": ["integer", "null"], "description": "Item id from the reward list, or null for none."},
            "announcement": {"type": "string", "description": "One server-wide line, up to 120 characters."},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this quest suits this character."},
        },
        "required": ["title", "briefing", "objectives_text", "progress_text", "completion_text", "giver",
                     "target_creature", "kill_count", "reward_money_copper", "reward_item",
                     "announcement", "dm_note"],
    },
}


class Rejected(Exception):
    """The model's quest broke a rule this script enforces."""


class NoContext(Exception):
    """There is nothing sensible to offer this character right now."""


def gather(name):
    who = world_query.character(name)
    if not who:
        raise NoContext(f"no character named {name}")
    context = {
        "character": who,
        "givers": world_query.givers(who),
        "targets": world_query.targets(who),
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
    lines += ["", "Nearby creatures (id, name, level, alive now, yards away):"]
    for t in context["targets"]:
        level = t["min_level"] if t["min_level"] == t["max_level"] else f"{t['min_level']}-{t['max_level']}"
        lines.append(f"- {t['creature']}: {t['name']}, level {level}, {t['alive']} alive, {t['distance']} yards")
    lines += ["", f"Maximum kill count: {MAX_KILLS}.", f"Money cap: {context['money_cap']} copper.", ""]
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


def build_spec(answer, context, quest_id):
    """Turn the model's answer into a full quest spec, enforcing every rule."""
    who = context["character"]
    by_id = {t["creature"]: t for t in context["targets"]}
    giver = {g["creature"]: g for g in context["givers"]}.get(answer.get("giver"))
    if not giver:
        raise Rejected(f"herald {answer.get('giver')} is not in the nearby NPCs list")
    target = by_id.get(answer.get("target_creature"))
    if not target:
        raise Rejected(f"target {answer.get('target_creature')} is not in the nearby creatures list")
    count = answer.get("kill_count")
    if not isinstance(count, int) or not 1 <= count <= min(MAX_KILLS, target["alive"]):
        raise Rejected(f"kill count {count} is outside 1 to {min(MAX_KILLS, target['alive'])} for {target['name']}")
    money = answer.get("reward_money_copper")
    if not isinstance(money, int) or not 0 <= money <= context["money_cap"]:
        raise Rejected(f"money {money} is outside 0 to {context['money_cap']} copper")
    item = answer.get("reward_item")
    allowed_items = {r["item"] for r in context["reward_items"]}
    if item is not None and item not in allowed_items:
        raise Rejected(f"reward item {item} is not in the reward list")
    announcement = str(answer.get("announcement", "")).strip()
    if len(announcement) > 200 or any(ord(ch) < 32 for ch in announcement):
        raise Rejected("announcement is too long or contains a line break")

    spec = {
        "id": quest_id,
        "title": answer.get("title"),
        "zone": who["zone"],
        "min_level": max(1, who["level"] - 2),
        "quest_level": max(who["level"], target["max_level"]),
        "giver": giver["creature"],
        "ender": giver["creature"],
        "briefing": answer.get("briefing"),
        "objectives_text": answer.get("objectives_text"),
        "progress_text": answer.get("progress_text"),
        "completion_text": answer.get("completion_text"),
        "kill": [{"creature": target["creature"], "count": count}],
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
    target = dict(target, giver=giver)          # carried along for display and the DM's records
    return spec, target, announcement


def show(spec, target, announcement, answer, context, usage):
    item_names = {r["item"]: r["name"] for r in context["reward_items"]}
    reward = [f"{spec['reward']['money_copper']} copper"]
    reward += [item_names[i["item"]] for i in spec["reward"]["items"]]
    print(f"""
--- Proposed quest {spec['id']} -------------------------------------------
Title:      {spec['title']}
For:        {context['character']['name']} (level {context['character']['level']} {context['character']['class_name']})
Herald:     {target['giver']['name']}, {target['giver']['distance']} yards {target['giver']['direction']}
Hunt:       {spec['kill'][0]['count']} x {target['name']} ({target['alive']} alive, {target['distance']} yards away)
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
