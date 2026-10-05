#!/usr/bin/env python3
"""The Overseer's loop: watch the players, remember, and propose what comes next.

    python3 dm.py tick                 one pass: observe, record, maybe propose
    python3 dm.py run --every 300      keep ticking every 300 seconds
    python3 dm.py pending              list proposals waiting for you
    python3 dm.py approve 3            put proposal 3 live
    python3 dm.py reject 3 "too easy"  discard it, with a reason the story keeps
    python3 dm.py story Zachadin       the chronicle for one character
    python3 dm.py arc Zachadin         the Overseer's private plan for a character (a spoiler)
    python3 dm.py arc Zachadin --seed "lead this priest down a dark path"
    python3 dm.py context Zachadin     what the model would be told next; no model call
    python3 dm.py pause "why"          the kill switch: nothing reaches the game
    python3 dm.py resume               undo it
    python3 dm.py purge                take every DM quest back out of the game

One bounty at a time: a character with a bounty offered or accepted gets no new proposal.
An accepted bounty never expires; one that is never accepted is dropped after DM_STALE_HOURS.
A character gets a mini-arc before their first bounty, and every bounty serves the arc in force.
A mini-arc covers a few levels. It ends when its finale is turned in or the character outgrows
it, and the next one is planned from how it ended.
Quest givers are not configured: each bounty is offered through a nearby friendly NPC the model
picks from a list built from the live world.

Unless you set DM_AUTO_APPROVE, a tick never puts a quest live. It only:
  - forces a save and reads the game databases,
  - writes what it noticed to its own memory (state.db),
  - notes who is in a party with whom, and who is nearby while a bounty is under way,
  - stops offering a bounty once the character it was written for turns it in,
  - asks the model for the next bounty when a character has none, and stores
    that as a pending proposal.
You approve proposals. That is the only step that adds a quest to the game.

Settings, on top of the ones the other scripts use:
    DM_COOLDOWN_MINUTES     wait after a bounty is finished before the next (default 20)
    DM_STALE_HOURS          an offered bounty nobody accepts is dropped after this (default 24)
    DM_MAX_PROPOSALS_HOUR   ceiling on model calls per hour (default 6)
    DM_AUTO_APPROVE         proposal types that go live without review, comma separated:
                            bounty, arc, letter (default: letter)
    DM_CHARACTERS           if set, the only characters the DM notices, comma separated.
                            Set this on a playerbot realm, where a thousand characters
                            exist and only a few of them are people
    DM_IGNORE_CHARACTERS    names the DM should not track, comma separated
    DM_MAIL_CHARACTER       name of a character the DM owns; mail sent to it is read as
                            letters to the Overseer (optional)
    DM_PAUSE                set to 1 to pause without the flag file (`pause` writes the file)
    DM_PAUSE_FILE           where the flag file lives (default: `paused` beside this script)

Paused means paused: no tick, no model call, no quest written and no console
command sent, whichever script is run. Reading is unaffected, so `story`,
`arc`, `context` and `pending` still answer.
"""
import argparse
import copy
import json
import os
import re
import sys
import time

import apply_quest
import console
import dm_state
import hot_quest
import llm
import world_query
import write_quest

CONTINUITY = """

Continuity:
- You are running an ongoing story for this character, not a list of chores. Read the story so far, the earlier bounties and what the character has done since, and make this bounty the next step: react to what they did, then escalate, turn or pay something off.
- Refer back to an earlier event at least once, in the briefing or the completion text. If this is the first contact, establish why the Overseer has taken an interest.
- Do not reuse the target of either of the last two bounties unless the story calls for it.
- If the character ignored or was slow with the last bounty, the Overseer has noticed.
- Other named characters in these notes are real players. When someone helped this character, was helped by them, or claimed a bounty meant for them, use it: the Overseer notices alliances and debts. Name them plainly in the text. Never invent a player.
- You may have a private arc for this character (shown below when there is one). Each bounty should serve its current beat: nudge toward the lure, bring the adversary closer, foreshadow the signature reward. Do it through what the herald says and what is hunted. Never state the plan to the player. If nothing nearby fits the beat, keep the thread alive in the text.
- beat_progress: "advance" if this bounty begins the next beat because the character has done enough or outgrown the level band, "hold" to stay on the current beat, "detour" if this bounty steps aside from the arc because of something the character did, "conclude" if this bounty is the arc's finale. Use "conclude" only on the last beat: turning it in ends the arc, so make it a payoff or a twist, and offer the signature reward if it is on the reward list. With no arc, answer "hold".
- story_beat: one sentence, past tense, the Overseer's view of what this bounty adds to the story.
- story_so_far: rewrite the running summary in at most 120 words: who this character is becoming in the Overseer's eyes, what has happened, and one or two open threads. It is your only memory next time, so keep what matters and drop what does not."""

SYSTEM = write_quest.RULES + CONTINUITY + write_quest.CLOSING

TOOL = copy.deepcopy(write_quest.TOOL)
TOOL["input_schema"]["properties"]["story_beat"] = {
    "type": "string", "description": "One sentence, past tense: what this bounty adds to the story."}
TOOL["input_schema"]["properties"]["story_so_far"] = {
    "type": "string", "description": "The rewritten running summary, at most 120 words."}
TOOL["input_schema"]["properties"]["beat_progress"] = {
    "type": "string", "enum": ["advance", "hold", "detour", "conclude"],
    "description": "How this bounty relates to the arc's current beat."}
TOOL["input_schema"]["required"] += ["story_beat", "story_so_far", "beat_progress"]

ARC_SYSTEM = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You are deciding in secret what you want to make of one character next.

Write a mini-arc: a short private plan that covers only the next few levels, about four to eight. A character's story is a chain of these. Each one ends, and what happened in it shapes the next, so the story can twist.

- premise: one or two sentences on what the Overseer sees in this character now.
- lure: what you want them to become or to do in this stretch. It can tempt, corrupt, ennoble or test, and it should be specific to their race and class.
- adversary: one enemy group or kind of creature from Azeroth as it is in 1.12 that this stretch turns toward. It must suit the levels just ahead and be reachable for their faction.
- beats: two to four steps. Each has a level band such as "6-9" and one sentence of intent. The first beat starts at the character's current level, the bands stay within about eight levels of it, and the last beat is a payoff or a twist that leaves a hook for whatever comes next.
- signature_reward: the name of one book from the list given, or an empty string. It is the prize of this arc. Prefer a power that is not the character's own by class when it fits the lure.
- previous_outcome: if an earlier arc has just ended, one or two sentences on how it actually ended, judged from the record of its bounties and not from how it was planned. Otherwise an empty string.
- If earlier arcs exist, this one must follow from their outcomes. Do not repeat an adversary unless the story calls for a return. A turn is welcome: an ally revealed as a rival, a victory with a cost, a temptation refused.
- If the server owner gives a direction, build the arc around it.

This plan is never shown to the player.

Respond by calling the submit_arc tool exactly once. Do not reply with prose."""

ARC_TOOL = {
    "name": "submit_arc",
    "description": "Submit the private mini-arc for this character.",
    "input_schema": {
        "type": "object",
        "properties": {
            "premise": {"type": "string", "description": "One or two sentences."},
            "lure": {"type": "string", "description": "What the Overseer wants this character to become or do."},
            "adversary": {"type": "string", "description": "The enemy group this stretch turns toward."},
            "beats": {
                "type": "array", "minItems": 2, "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "level_band": {"type": "string", "description": "Levels this beat covers, like 6-9."},
                        "intent": {"type": "string", "description": "One sentence: what this step does."},
                    },
                    "required": ["level_band", "intent"],
                },
            },
            "signature_reward": {"type": "string", "description": "A book name from the list, or an empty string."},
            "previous_outcome": {"type": "string", "description": "How the arc that just ended actually ended, or an empty string."},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this arc for this character."},
        },
        "required": ["premise", "lure", "adversary", "beats", "signature_reward", "previous_outcome", "dm_note"],
    },
}

ARC_REACH = 10          # an arc's bands and its signature reward stay within this many levels of the character

BEAT_STATE = ("done", "current", "later")


class ApproveFailed(Exception):
    pass


def auto_approved():
    """Proposal types that go live without waiting for a person."""
    return {part.strip().lower() for part in os.environ.get("DM_AUTO_APPROVE", "letter").split(",") if part.strip()}


def setting(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def ignored():
    names = os.environ.get("DM_IGNORE_CHARACTERS", "") + "," + os.environ.get("DM_MAIL_CHARACTER", "")
    return {part.strip().lower() for part in names.split(",") if part.strip()}


def watched():
    """Names the DM may notice, or None for everyone who is not ignored.

    A realm running playerbots holds a thousand characters or more, far too
    many to list in DM_IGNORE_CHARACTERS. DM_CHARACTERS turns the rule around:
    name the few real players and the DM notices nobody else, so no model call
    is ever spent writing an arc for a bot.

    Names, not guids, on purpose: a character deleted and made again keeps its
    name but takes a new guid, and the owner should not have to notice.
    """
    names = {part.strip().lower() for part in os.environ.get("DM_CHARACTERS", "").split(",") if part.strip()}
    return names or None


def noticed(who, allow, skip):
    """Whether the DM should pay this character any attention at all."""
    name = (who.get("name") or "").lower()
    if name in skip:
        return False
    return allow is None or name in allow


# ---- observe ---------------------------------------------------------------

def observe_character(db, who, party, say):
    """Compare one online character with what the DM last knew; record the differences.

    `party` is the list of other characters grouped with this one right now.
    """
    known = dm_state.get_character(db, who["guid"])
    party_now = [{"guid": m["guid"], "name": m["name"], "descr": world_query.describe(m)} for m in party]
    done_now = world_query.rewarded_quests(who["guid"])
    spells_now = world_query.borrowed_capstones(who)
    low, high = hot_quest.QUEST_ID_RANGE

    if known is None:
        dm_state.save_character(db, who, done_now.keys(), spells_now.keys(), party_now, first=True)
        text = (f"first noticed: a level {who['level']} {who['race_name']} {who['class_name']} "
                f"in {world_query.zone_name(who['zone'])}")
        if party_now:
            text += ", travelling with " + " and ".join(m["descr"] for m in party_now)
        dm_state.add_event(db, who["guid"], "first_seen", text)
        say(f"  {who['name']}: {text}")
        return

    noticed = []
    if who["level"] > known["level"]:
        noticed.append(("level", f"reached level {who['level']}"))
    if who["zone"] != known["zone"]:
        noticed.append(("travel", f"travelled from {world_query.zone_name(known['zone'])} "
                                  f"to {world_query.zone_name(who['zone'])}"))
    seen_quests = set(json.loads(known["known_quests"]))
    fresh = [title for quest, title in done_now.items() if quest not in seen_quests and not low <= quest <= high]
    for title in fresh[:5]:
        noticed.append(("quest", f"completed the quest \"{title}\""))
    if len(fresh) > 5:
        noticed.append(("quest", f"completed {len(fresh) - 5} more quests"))
    seen_spells = set(json.loads(known["known_spells"]))
    for spell, name in spells_now.items():
        if spell not in seen_spells:
            noticed.append(("spell", f"learned {name}, a power that is not a {who['class_name']}'s by right"))

    before = {m["guid"]: m for m in json.loads(known["party"])}
    for member in party_now:
        if member["guid"] not in before:
            noticed.append(("party", f"joined forces with {member['descr']}"))
    for guid, member in before.items():
        if guid not in {m["guid"] for m in party_now}:
            noticed.append(("party", f"parted ways with {member['name']}"))

    for kind, text in noticed:
        dm_state.add_event(db, who["guid"], kind, text)
        say(f"  {who['name']}: {text}")
    dm_state.save_character(db, who, done_now.keys(), spells_now.keys(), party_now)


def observe_company(db, players, parties, progress):
    """While a bounty is under way, note who is with the character it was written for.

    Three kinds of company are recorded against the bounty:
      party   in the same party
      shared  also holds or finished the same bounty
      nearby  online within 150 yards, not in the party
    A tick is a sample, not a recording: someone who helped only between two
    ticks is missed, and "nearby" does not prove they fought.
    """
    online = {who["guid"]: who for who in players}
    holders = {}
    for row in progress:
        holders.setdefault(row["quest"], []).append(row)
    for quest in db.execute("SELECT * FROM quests WHERE status IN ('offered', 'accepted')").fetchall():
        rows = holders.get(quest["quest"], [])
        if not any(row["guid"] == quest["guid"] for row in rows):
            continue                                    # the intended character has not taken it yet
        in_party = parties.get(quest["guid"], [])
        for member in in_party:
            dm_state.note_companion(db, quest["quest"], member, world_query.describe(member), "party")
        for row in rows:
            if row["guid"] != quest["guid"]:
                other = online.get(row["guid"]) or dm_state.get_character(db, row["guid"])
                descr = world_query.describe(other) if other else row["name"]
                dm_state.note_companion(db, quest["quest"], row, descr, "shared")
        who = online.get(quest["guid"])
        if who:
            grouped = {member["guid"] for member in in_party}
            for other in world_query.nearby(who, players):
                if other["guid"] not in grouped:
                    dm_state.note_companion(db, quest["quest"], other, world_query.describe(other), "nearby")


def circumstances(db, quest, completer, took):
    """One paragraph on how a bounty ended: who, where, how long, and in what company."""
    where = world_query.zone_name(completer["zone"])
    text = f"{world_query.describe(completer)}, turned it in while in {where}, {took} after it was posted."
    company = dm_state.companions_of(db, quest["quest"])
    listed = {completer["guid"]}                        # name each person once, under their closest tie
    for how, lead in (("party", "In the party during the hunt: "), ("shared", "Also took the same bounty: "),
                      ("nearby", "Seen close by during the hunt, not in the party: ")):
        rows = [row for row in company if row["how"] == how and row["guid"] not in listed]
        if rows:
            text += " " + lead + "; ".join(row["descr"] for row in rows) + "."
            listed.update(row["guid"] for row in rows)
    if not company:
        text += " No one else was seen with them."
    return text


def observe_bounties(db, players, progress, say):
    """Follow each live bounty: accepted, finished, or left to rot."""
    online = {who["guid"]: who for who in players}
    # Every bounty, including finished ones: someone else may turn one in later.
    live = {row["quest"]: row for row in db.execute("SELECT * FROM quests")}

    def seen(marker):
        return db.execute("SELECT 1 FROM events WHERE text LIKE ?", (f"%[{marker}]",)).fetchone()

    for row in progress:
        quest = live.get(row["quest"])
        if not quest:
            continue
        intended = row["guid"] == quest["guid"]
        marker = f"{row['quest']}:{row['guid']}:{row['state']}"
        if seen(marker):
            continue
        owner = dm_state.get_character(db, quest["guid"])
        owner_descr = world_query.describe(owner) if owner else "someone else"

        if row["state"] == "accepted":
            text = f"accepted the bounty \"{quest['title']}\""
            if intended and quest["status"] == "offered":
                db.execute("UPDATE quests SET status = 'accepted', accepted_at = ? WHERE quest = ?",
                           (dm_state.now(), quest["quest"]))
        elif row["state"] == "ready":
            text = f"finished the hunt for \"{quest['title']}\" and has not yet returned to the herald"
        else:
            took = dm_state.ago(quest["issued_at"])
            text = f"turned in the bounty \"{quest['title']}\", {took} after it was posted"
            completer = online.get(row["guid"]) or dm_state.get_character(db, row["guid"])
            if intended and completer:
                summary = circumstances(db, quest, completer, took)
                db.execute("UPDATE quests SET status = 'completed', completed_at = ?, completed_by = ?, "
                           "circumstances = ? WHERE quest = ?",
                           (dm_state.now(), completer["name"], summary, quest["quest"]))
                helpers = [c for c in dm_state.companions_of(db, quest["quest"]) if c["how"] in ("party", "shared")]
                if helpers:
                    text += ", with " + " and ".join(sorted({c["name"] for c in helpers})) + " alongside"
                for helper in helpers:
                    helped = f"{quest['quest']}:{helper['guid']}:helped"
                    if not seen(helped):
                        role = "in the same party" if helper["how"] == "party" else "having taken the same bounty"
                        dm_state.add_event(db, helper["guid"], "bounty",
                                           f"helped {owner_descr} with the bounty \"{quest['title']}\", {role} [{helped}]")
            elif not intended:
                claimed = f"{quest['quest']}:{row['guid']}:claimed"
                if not seen(claimed):
                    dm_state.add_event(db, quest["guid"], "bounty",
                                       f"the bounty \"{quest['title']}\", written for them, was also turned in "
                                       f"by {row['name']} [{claimed}]")
        if not intended:
            text += f" (it was written for {owner_descr})"
        dm_state.add_event(db, row["guid"], "bounty", f"{text} [{marker}]")
        say(f"  {row['name']}: {text}")
        if intended and row["state"] == "done":
            ended = db.execute("SELECT circumstances FROM quests WHERE quest = ?", (quest["quest"],)).fetchone()[0]
            say(f"    how it ended: {ended}")
            if not quest["retired_at"]:
                retire(db, quest, say)
            finale = db.execute("SELECT * FROM arcs WHERE id = ? AND status = 'active'",
                                (quest["concludes_arc"],)).fetchone() if quest["concludes_arc"] else None
            if finale:
                dm_state.end_arc(db, finale, "resolved")
                say(f"  {row['name']}: the arc against {finale['adversary']} has ended; a new one will be planned")

    stale = dm_state.now() - int(setting("DM_STALE_HOURS", 24) * 3600)
    for quest in db.execute("SELECT * FROM quests WHERE status = 'offered' AND issued_at < ?", (stale,)).fetchall():
        db.execute("UPDATE quests SET status = 'ignored' WHERE quest = ?", (quest["quest"],))
        dm_state.add_event(db, quest["guid"], "bounty", f"ignored the bounty \"{quest['title']}\"")
        say(f"  bounty \"{quest['title']}\" was ignored")
        retire(db, quest, say)


def retire(db, quest, say):
    """Stop offering a bounty. History stays, in the game and here."""
    try:
        apply_quest.apply_spec(json.loads(quest["spec"]), retire=True, say=lambda _line: None)
        db.execute("UPDATE quests SET retired_at = ? WHERE quest = ?", (dm_state.now(), quest["quest"]))
        say(f"  bounty \"{quest['title']}\" is no longer offered")
    except (apply_quest.StepFailed, console.ConsoleError, hot_quest.SpecError) as error:
        say(f"  could not retire \"{quest['title']}\": {error}")


def observe_letters(db, say):
    name = os.environ.get("DM_MAIL_CHARACTER", "").strip()
    if not name:
        return
    for letter in world_query.letters_to(name):
        if db.execute("SELECT 1 FROM seen_letters WHERE id = ?", (letter["id"],)).fetchone():
            continue
        db.execute("INSERT INTO seen_letters (id) VALUES (?)", (letter["id"],))
        body = " ".join(str(letter["body"]).split())[:400]
        text = f"wrote to the Overseer, subject \"{letter['subject']}\": {body}"
        dm_state.add_event(db, letter["from_guid"], "letter", text)
        say(f"  {letter['from']}: {text}")


# ---- remember and propose ---------------------------------------------------

def story_section(db, who):
    """Everything the model needs to carry the story forward for one character."""
    known = dm_state.get_character(db, who["guid"])
    history = dm_state.quest_history(db, who["guid"])
    lines = ["Story so far (your own summary from last time):",
             known["story_so_far"] or "Nothing yet. This is the Overseer's first contact with this character.", ""]

    if history:
        lines.append("Earlier bounties, oldest first:")
        for quest in history:
            spec = json.loads(quest["spec"])
            names = json.loads(quest["objectives"] or "null")
            hunt = write_quest.objective_summary(spec, names)
            if quest["kind"]:
                hunt = f"{quest['kind']}: {hunt}"
            outcome = {
                "completed": f"turned in after {dm_state.ago(quest['issued_at'], quest['completed_at'])}",
                "accepted": "accepted, not finished yet",
                "offered": "posted, not yet accepted",
                "ignored": "ignored; it expired unaccepted",
                "purged": "withdrawn by the server owner",
            }.get(quest["status"], quest["status"])
            through = f", offered through {quest['giver']}" if quest["giver"] else ""
            lines.append(f"- \"{quest['title']}\" ({hunt}){through}, posted {dm_state.ago(quest['issued_at'])} ago: "
                         f"{outcome}. Beat: {quest['story_beat']}")
            if quest["circumstances"]:
                lines.append(f"  How it ended: {quest['circumstances']}")
        lines.append("")

    party = json.loads(known["party"])
    allies = db.execute(
        "SELECT c.name, c.descr, c.how, COUNT(DISTINCT c.quest) AS bounties FROM companions c "
        "JOIN quests q ON q.quest = c.quest WHERE q.guid = ? AND c.how IN ('party', 'shared') "
        "GROUP BY c.guid ORDER BY bounties DESC LIMIT 5", (who["guid"],)).fetchall()
    if party or allies:
        lines.append("Company (real players):")
        if party:
            lines.append("- in a party right now with " + " and ".join(m["descr"] for m in party))
        for ally in allies:
            lines.append(f"- {ally['descr']} has been alongside for {ally['bounties']} of this character's bounties")
        lines.append("")

    since = history[-1]["issued_at"] if history else 0
    events = dm_state.events_since(db, who["guid"], since)
    lines.append("What the character has done since your last bounty:" if history else "What you know of the character:")
    if events:
        lines += [f"- {event['text'].split(' [')[0]}" for event in events]
    else:
        lines.append("- nothing new")

    saga = saga_lines(db, who["guid"])
    if saga:
        lines += ["", "Earlier arcs, now ended, oldest first:"] + saga
    arc = dm_state.active_arc(db, who["guid"])
    if arc:
        lines += ["", arc_text(arc, "Your private arc for this character (never state it to the player):")]

    rejected = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                          "AND type = 'bounty' ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if rejected:
        lines += ["", "The server owner turned down your recent ideas for this character, saying:"]
        lines += [f"- {row['reason']}" for row in rejected]
    return "\n".join(lines)


def arc_text(arc, heading):
    """An arc as plain text, with each beat marked done, current or later."""
    beats = json.loads(arc["beats"]) if isinstance(arc["beats"], str) else arc["beats"]
    current = arc["current_beat"] if "current_beat" in arc.keys() else 0
    lines = [heading, f"Premise: {arc['premise']}", f"Lure: {arc['lure']}", f"Adversary: {arc['adversary']}",
             f"Signature reward: {arc['signature_reward'] or 'none chosen'}", "Beats:"]
    for index, beat in enumerate(beats):
        state = BEAT_STATE[0] if index < current else BEAT_STATE[1] if index == current else BEAT_STATE[2]
        lines.append(f"{index + 1}. [{state}] levels {beat['level_band']}: {beat['intent']}")
    return "\n".join(lines)


def validate_arc(answer, book_names, level=None):
    """Check the model's arc and return it in stored form. Raises write_quest.Rejected."""
    arc = {}
    for field, limit in (("premise", 400), ("lure", 400), ("adversary", 120)):
        text = " ".join(str(answer.get(field, "")).split())
        if not text or len(text) > limit:
            raise write_quest.Rejected(f"arc {field} is missing or over {limit} characters")
        arc[field] = text
    beats = answer.get("beats")
    if not isinstance(beats, list) or not 2 <= len(beats) <= 4:
        raise write_quest.Rejected("a mini-arc needs two to four beats")
    arc["beats"] = []
    for beat in beats:
        band = "".join(str(beat.get("level_band", "")).split()) if isinstance(beat, dict) else ""
        intent = " ".join(str(beat.get("intent", "")).split()) if isinstance(beat, dict) else ""
        if not re.fullmatch(r"\d{1,2}-\d{1,2}", band) or not intent or len(intent) > 300:
            raise write_quest.Rejected("each beat needs a level band like 6-9 and one sentence of intent")
        low, high = (int(part) for part in band.split("-"))
        if low > high or (level is not None and high > level + ARC_REACH):
            raise write_quest.Rejected(f"beat band {band} runs backwards or reaches more than {ARC_REACH} levels ahead")
        arc["beats"].append({"level_band": band, "intent": intent})
    wanted = " ".join(str(answer.get("signature_reward", "")).split())
    by_lower = {name.lower(): name for name in book_names}
    if wanted and wanted.lower() not in by_lower:
        raise write_quest.Rejected(f"signature reward \"{wanted}\" is not one of the books on the list")
    arc["signature_reward"] = by_lower.get(wanted.lower(), "")
    arc["previous_outcome"] = " ".join(str(answer.get("previous_outcome", "")).split())[:500]
    arc["dm_note"] = " ".join(str(answer.get("dm_note", "")).split())
    return arc


def saga_lines(db, guid):
    """Earlier arcs as short lines: what was planned and how each ended."""
    lines = []
    for arc in dm_state.past_arcs(db, guid):
        ended = {"resolved": "its finale was turned in", "outgrown": "the character outgrew it unfinished",
                 "replaced": "the server owner replaced it"}.get(arc["end_reason"], "it ended")
        lines.append(f"- Against {arc['adversary']}: {arc['lure']} Ended: {ended}."
                     + (f" Outcome: {arc['outcome']}" if arc["outcome"] else ""))
    return lines


def arc_message(db, who, books, seed):
    known = dm_state.get_character(db, who["guid"])
    lines = [f"Character: {world_query.describe(who)}, currently in {world_query.zone_name(who['zone'])}.", ""]

    saga = saga_lines(db, who["guid"])
    if saga:
        lines += ["Earlier arcs for this character, oldest first:"] + saga + [""]
        last = dm_state.past_arcs(db, who["guid"])[-1]
        if not last["outcome"]:
            lines.append("The arc that just ended, as planned:")
            lines.append(arc_text(last, "(write previous_outcome for this one)"))
            during = db.execute("SELECT * FROM quests WHERE guid = ? AND issued_at >= ? ORDER BY issued_at",
                                (who["guid"], last["created_at"])).fetchall()
            lines.append("What actually happened in its bounties:")
            for quest in during:
                how = quest["circumstances"] or f"status: {quest['status']}"
                lines.append(f"- \"{quest['title']}\" ({quest['target']}). Beat: {quest['story_beat']} {how}")
            if not during:
                lines.append("- no bounty was posted under it")
            lines.append("")

    events = dm_state.events_since(db, who["guid"], 0, limit=30)
    lines.append("What you know of them so far:")
    lines += [f"- {event['text'].split(' [')[0]}" for event in events] or ["- nothing yet"]
    if known and known["story_so_far"]:
        lines += ["", "Story so far (your own summary):", known["story_so_far"]]
    lines += ["", "Books a signature reward may be chosen from (name, level needed to use it):"]
    lines += [f"- {book['name']}, level {book['required_level']}" for book in books] or ["- none available"]
    turned_down = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                             "AND type = 'arc' ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if turned_down:
        lines += ["", "The server owner turned down your earlier arcs for this character, saying:"]
        lines += [f"- {row['reason']}" for row in turned_down]
    if seed:
        lines += ["", f"Direction from the server owner: {seed}"]
    return "\n".join(lines)


def propose_arc(db, who, seed, say):
    """Ask the model for an arc and store it as a proposal. Returns the proposal number."""
    books = world_query.reward_items(who, reach=ARC_REACH, limit=40)
    answer, usage = llm.ask_for_tool_call(ARC_SYSTEM, arc_message(db, who, books, seed), ARC_TOOL, max_tokens=4096)
    arc = validate_arc(answer, [book["name"] for book in books], who["level"])
    arc["seed"] = seed or ""
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, dm_note, model, tokens_in, tokens_out) "
               "VALUES (?, ?, ?, 'arc', ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(arc), arc["dm_note"], usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: arc proposal {number} written (adversary: {arc['adversary']})")
    return number


def arc_outgrown(arc, level):
    """Has the character levelled past the arc's last beat?"""
    last_band = json.loads(arc["beats"])[-1]["level_band"]
    return level > int(last_band.split("-")[1]) + 1


def wants_bounty(db, who):
    """Why this character should not get a proposal right now, or None if it should."""
    if dm_state.pending_proposal(db, who["guid"], "bounty"):
        return "a proposal is already waiting for approval"
    if dm_state.open_quest(db, who["guid"]):
        return "a bounty is already out"
    last = dm_state.last_quest(db, who["guid"])
    if last:
        finished = last["completed_at"] or last["retired_at"] or last["issued_at"]
        wait = setting("DM_COOLDOWN_MINUTES", 20) * 60 - (dm_state.now() - finished)
        if wait > 0:
            return f"cooling down for another {round(wait / 60)} minutes"
    return None


def recent_targets(db, guid, count=2):
    """Creature ids from this character's last few bounties, so a target is not reused.

    Read from each bounty's spec, where the creature is an id. The `target`
    column holds only the creature's name.
    """
    found = []
    for quest in dm_state.quest_history(db, guid, limit=count):
        try:
            spec = json.loads(quest["spec"] or "{}")
        except json.JSONDecodeError:
            continue
        for objective in spec.get("kill") or []:
            if objective.get("creature"):
                found.append(int(objective["creature"]))
    return found


def props_in_use(db):
    """Props a bounty still out has already seeded into a creature's loot.

    A quest-only drop fires for anyone whose quest needs that item, so two
    live bounties sharing a prop would let each character loot the other's.
    """
    found = set()
    for row in db.execute("SELECT spec FROM quests WHERE status IN ('offered', 'accepted')"):
        try:
            spec = json.loads(row["spec"] or "{}")
        except json.JSONDecodeError:
            continue
        for prop in spec.get("props") or []:
            if prop.get("item"):
                found.add(int(prop["item"]))
    return found


def propose(db, who, say):
    """Ask the model for the next bounty and store it as a proposal. Returns the proposal number."""
    context = write_quest.gather(who["name"], skip=recent_targets(db, who["guid"]),
                                 props_in_use=props_in_use(db))
    message = write_quest.user_message(context, None, story=story_section(db, context["character"]))
    answer, usage = llm.ask_for_tool_call(SYSTEM, message, TOOL, max_tokens=4096)
    spec, target, announcement = write_quest.build_spec(answer, context, hot_quest.QUEST_ID_RANGE[0])
    beat = " ".join(str(answer.get("story_beat", "")).split())
    summary = " ".join(str(answer.get("story_so_far", "")).split())
    if not beat or not summary:
        raise write_quest.Rejected("the model left out the story beat or the story summary")
    if len(summary.split()) > 160:
        raise write_quest.Rejected("the story summary is far over 120 words")
    progress = answer.get("beat_progress")
    if progress not in ("advance", "hold", "detour", "conclude"):
        progress = "hold"
    arc = dm_state.active_arc(db, who["guid"])
    payload = {"beat_progress": progress, "kind": target["kind"], "needs_party": target["needs_party"]}
    if progress == "conclude":
        if arc and arc["current_beat"] >= len(json.loads(arc["beats"])) - 1:
            payload["concludes_arc"] = arc["id"]
        else:
            payload["beat_progress"] = "hold"       # a finale before the last beat is just another bounty
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, spec, target, announcement, dm_note, story_beat, "
               "story_so_far, model, tokens_in, tokens_out) VALUES (?, ?, ?, 'bounty', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(payload), json.dumps(spec),
                json.dumps(target), announcement, answer.get("dm_note", ""), beat, summary, usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: bounty proposal {number} written, \"{spec['title']}\"")
    return number


def approve_proposal(db, row, say):
    """Carry out one pending proposal. Raises ApproveFailed if nothing was done."""
    stamp = dm_state.now()
    if row["type"] == "arc":
        arc = json.loads(row["payload"])
        in_force = dm_state.active_arc(db, row["guid"])
        if in_force:                                    # a reseed replaces the arc in force
            dm_state.end_arc(db, in_force, "replaced")
        ended = dm_state.past_arcs(db, row["guid"])
        if ended and not ended[-1]["outcome"] and arc.get("previous_outcome"):
            db.execute("UPDATE arcs SET outcome = ? WHERE id = ?", (arc["previous_outcome"], ended[-1]["id"]))
        db.execute("INSERT INTO arcs (guid, premise, lure, adversary, beats, signature_reward, seed, model, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (row["guid"], arc["premise"], arc["lure"], arc["adversary"], json.dumps(arc["beats"]),
                    arc["signature_reward"], arc.get("seed", ""), row["model"], stamp, stamp))
        db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
        db.commit()
        say(f"arc for {row['name']} is now in force.")
        return

    payload = json.loads(row["payload"] or "{}")
    target = json.loads(row["target"])
    if payload.get("needs_party") or payload.get("kind") == "party":
        # 1.1 offers an elite, and 1.2 offers a party bounty, on the strength of
        # who was grouped and online when the bounty was written. A party can
        # disband in between, so the company is checked again here.
        company = [other for other in world_query.parties().get(row["guid"], []) if other.get("online")]
        if not company:
            raise ApproveFailed(f"this bounty was written for a party and {row['name']} is now alone; "
                                "reject it and the next tick will write another")
    spec = json.loads(row["spec"])
    spec["id"] = world_query.next_quest_id()        # the id is fixed only now, so it cannot collide
    try:
        apply_quest.apply_spec(spec, announce=row["announcement"], say=lambda _line: None)
    except (apply_quest.StepFailed, hot_quest.SpecError) as error:
        raise ApproveFailed(str(error)) from None
    except console.ConsoleError as error:
        say(f"warning: the quest is in the database but the console step failed: {error}\n"
            f"  Run `.reload all_quest` in game to load it.")
    db.execute("INSERT INTO quests (quest, guid, title, kind, objectives, target, giver, concludes_arc, spec, "
               "announcement, dm_note, story_beat, model, issued_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (spec["id"], row["guid"], spec["title"], payload.get("kind"),
                json.dumps(target.get("objectives") or []), target["name"],
                (target.get("giver") or {}).get("name"),
                payload.get("concludes_arc"), json.dumps(spec), row["announcement"], row["dm_note"],
                row["story_beat"], row["model"], stamp))
    db.execute("UPDATE characters SET story_so_far = ? WHERE guid = ?", (row["story_so_far"], row["guid"]))
    db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
    dm_state.add_event(db, row["guid"], "overseer", f"the Overseer posted the bounty \"{spec['title']}\"")
    arc = dm_state.active_arc(db, row["guid"])
    progress = payload.get("beat_progress", "hold")
    if arc and progress == "advance":
        last = len(json.loads(arc["beats"])) - 1
        db.execute("UPDATE arcs SET current_beat = ?, updated_at = ? WHERE id = ?",
                   (min(arc["current_beat"] + 1, last), stamp, arc["id"]))
    db.commit()
    say(f"quest {spec['id']} is live for {row['name']}; the story has moved on.")


def settle(db, number, say):
    """Approve a fresh proposal on the spot if its type is set to go without review."""
    row = db.execute("SELECT * FROM proposals WHERE id = ?", (number,)).fetchone()
    if row["type"] not in auto_approved():
        say(f"    waiting for you: python3 dm.py pending")
        return False
    try:
        approve_proposal(db, row, lambda line: say(f"    auto-approved: {line}"))
        return True
    except ApproveFailed as error:
        say(f"    auto-approval failed, left pending: {error}")
        return False


def tick(db, say=print):
    stopped = console.paused()
    if stopped:
        say(f"tick: the DM is paused ({stopped}). Nothing observed, nothing proposed.")
        say("resume with: python3 dm.py resume")
        return
    try:
        console.run("saveall")
    except console.ConsoleError as error:
        say(f"note: could not refresh positions ({error}); using the last save")

    skip, allow = ignored(), watched()
    online = world_query.online_characters()
    players = [who for who in online if noticed(who, allow, skip)]
    if allow is not None and len(online) != len(players):
        say(f"tick: {len(players)} of {len(online)} character(s) online are tracked (DM_CHARACTERS)")
    else:
        say(f"tick: {len(players)} character(s) online")
    parties = world_query.parties()
    progress = world_query.dm_quest_progress()
    for who in players:
        company = [m for m in parties.get(who["guid"], []) if noticed(m, allow, skip)]
        observe_character(db, who, company, say)
    observe_company(db, players, parties, progress)
    observe_bounties(db, players, progress, say)
    observe_letters(db, say)
    db.commit()

    ceiling = setting("DM_MAX_PROPOSALS_HOUR", 6)
    for who in players:
        try:
            # A mini-arc the character has levelled past is closed, so the next one can be planned.
            arc = dm_state.active_arc(db, who["guid"])
            if arc and not dm_state.open_quest(db, who["guid"]) and arc_outgrown(arc, who["level"]):
                dm_state.end_arc(db, arc, "outgrown")
                say(f"  {who['name']}: outgrew the arc against {arc['adversary']}; a new one will be planned")
            # An arc comes first: no bounty is written for a character without an approved plan.
            if not dm_state.active_arc(db, who["guid"]):
                if dm_state.pending_proposal(db, who["guid"], "arc"):
                    say(f"  {who['name']}: no proposal (an arc is waiting for approval)")
                    continue
                if dm_state.proposals_in_last_hour(db) >= ceiling:
                    say("  hourly ceiling on model calls reached; no more proposals this tick")
                    break
                if not settle(db, propose_arc(db, who, None, say), say):
                    db.commit()
                    continue
            reason = wants_bounty(db, who)
            if reason:
                say(f"  {who['name']}: no proposal ({reason})")
                continue
            if dm_state.proposals_in_last_hour(db) >= ceiling:
                say("  hourly ceiling on model calls reached; no more proposals this tick")
                break
            settle(db, propose(db, who, say), say)
        except write_quest.NoContext as error:
            say(f"  {who['name']}: no proposal ({error})")
        except (write_quest.Rejected, hot_quest.SpecError) as error:
            say(f"  {who['name']}: the model's answer was rejected ({error}); it will try again next tick")
        except llm.LLMError as error:
            say(f"  {who['name']}: model call failed ({error})")
        db.commit()


# ---- commands ---------------------------------------------------------------

def show_proposal(row):
    if row["type"] == "arc":
        arc = json.loads(row["payload"])
        print(f"\n=== Proposal {row['id']}: ARC for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===")
        print(arc_text(arc, "A private plan. Reading it is a spoiler if you play this character."))
        if arc.get("seed"):
            print(f"Your direction: {arc['seed']}")
        print(f"Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]")
        return
    spec, target = json.loads(row["spec"]), json.loads(row["target"])
    reward = [f"{spec['reward']['money_copper']} copper"] + [f"item {i['item']}" for i in spec["reward"]["items"]]
    payload = json.loads(row["payload"] or "{}")
    progress = payload.get("beat_progress", "hold")
    kind = payload.get("kind") or target.get("kind") or "hunt"
    if payload.get("needs_party"):
        kind += " (needs the party; checked again at approval)"
    objectives = "\n          ".join(write_quest.objective_lines(target))
    giver = target.get("giver")
    herald = f"{giver['name']}, {giver['distance']} yards {giver['direction']}" if giver else "unknown"
    print(f"""
=== Proposal {row['id']}: BOUNTY for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===
Title:    {spec['title']}
Kind:     {kind}
Herald:   {herald}
Hunt:     {objectives}
Reward:   {', '.join(reward)}
Briefing:
  {spec['briefing']}
Objectives:
  {spec['objectives_text']}
At turn-in:
  {spec['completion_text']}
Announcement: {row['announcement']}
Story beat:   {row['story_beat']}
Story so far: {row['story_so_far']}
Arc:          {progress}
Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]""")


def cmd_pending(db, _args):
    stopped = console.paused()
    if stopped:
        print(f"** the DM is paused ({stopped}). Nothing can reach the game until you resume. **\n")
    found = db.execute("SELECT * FROM proposals WHERE status = 'pending' ORDER BY id").fetchall()
    if not found:
        print("no proposals waiting.")
    for row in found:
        show_proposal(row)
    if found:
        print("\napprove with: python3 dm.py approve <number>    reject with: python3 dm.py reject <number> \"why\"")


def cmd_approve(db, args):
    stopped = console.paused()
    if stopped:
        sys.exit(f"dm: the DM is paused ({stopped}); nothing was applied.\n"
                 f"    resume with: python3 dm.py resume")
    row = db.execute("SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (args.number,)).fetchone()
    if not row:
        sys.exit(f"dm: no pending proposal {args.number}")
    try:
        approve_proposal(db, row, print)
    except ApproveFailed as error:
        sys.exit(f"dm: not applied: {error}")


def cmd_reject(db, args):
    row = db.execute("SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (args.number,)).fetchone()
    if not row:
        sys.exit(f"dm: no pending proposal {args.number}")
    db.execute("UPDATE proposals SET status = 'rejected', decided_at = ?, reason = ? WHERE id = ?",
               (dm_state.now(), args.reason or "", row["id"]))
    db.commit()
    print(f"proposal {row['id']} rejected. A new {row['type']} will be written on the next tick"
          + (", with your reason passed to the model." if args.reason else "."))


def cmd_arc(db, args):
    """Show a character's arcs, or have a new one written from a direction of yours."""
    who = world_query.character(args.character)
    if not who:
        sys.exit(f"dm: no character named {args.character}")
    if not dm_state.get_character(db, who["guid"]):
        sys.exit("dm: the Overseer has not noticed this character yet; run a tick while they are online")
    if args.seed:
        waiting = dm_state.pending_proposal(db, who["guid"], "arc")
        if waiting:
            sys.exit(f"dm: arc proposal {waiting['id']} is already waiting; approve or reject it first")
        try:
            settle(db, propose_arc(db, who, args.seed, print), print)
        except (write_quest.Rejected, llm.LLMError) as error:
            sys.exit(f"dm: no arc written: {error}")
        db.commit()
        return
    print(f"=== Arcs for {who['name']} (spoilers if you play this character) ===")
    saga = saga_lines(db, who["guid"])
    if saga:
        print("\nEnded, oldest first:")
        print("\n".join(saga))
    arc = dm_state.active_arc(db, who["guid"])
    if not arc:
        print("\nNo arc in force. One is written on the next tick, or give a direction with --seed.")
        return
    print("\n" + arc_text(arc, "In force:"))
    if arc["seed"]:
        print(f"Your direction: {arc['seed']}")


def cmd_pause(db, args):
    """The kill switch. Writes a flag file; every path to the game checks it."""
    reason = args.reason or "paused by hand"
    with open(console.pause_file(), "w", encoding="utf-8") as handle:
        handle.write(reason + "\n")
    dm_state.add_event(db, 0, "paused", reason)
    db.commit()
    print(f"paused: {reason}\n"
          "No tick will observe or propose, no quest will be written, no console command will be sent.\n"
          "Reading still works: story, arc, context and pending.\n"
          "resume with: python3 dm.py resume")


def cmd_resume(db, _args):
    stopped = console.paused()
    if not stopped:
        print("not paused.")
        return
    try:
        os.remove(console.pause_file())
    except OSError:
        pass
    if console.paused():            # DM_PAUSE in the environment, not the flag file
        sys.exit("dm: still paused because DM_PAUSE is set in the environment or .env; unset it to resume.")
    dm_state.add_event(db, 0, "resumed", f"was: {stopped}")
    db.commit()
    print("resumed. The next tick will observe and propose again.")


def live_dm_quests():
    """Every quest in the DM's id range that is in the world database right now.

    Read from the game, not from the DM's memory: a quest written by hand or by
    a run whose record was lost still has to be cleanable.
    """
    low, high = hot_quest.QUEST_ID_RANGE
    return world_query.rows(
        f"SELECT JSON_OBJECT('id', entry, 'title', Title) "
        f"FROM {hot_quest.WORLD_DB}.quest_template WHERE entry BETWEEN {low} AND {high} ORDER BY entry;")


def seeded_loot_rows():
    """Every loot row any DM bounty has added, by its tag. Read from the game,
    so a row left behind by a run whose record was lost is still found."""
    return world_query.rows(
        f"SELECT JSON_OBJECT('creature', entry, 'item', item, 'tag', comments) "
        f"FROM {hot_quest.WORLD_DB}.creature_loot_template "
        f"WHERE comments LIKE 'dm:%' ORDER BY comments;")


def cmd_purge(db, args):
    """Take every DM quest and every seeded prop out of the game. Memory is kept."""
    live = live_dm_quests()
    seeded = seeded_loot_rows()
    low, high = hot_quest.QUEST_ID_RANGE
    if not live and not seeded:
        print(f"nothing to purge: no quest in {low} to {high} is in the world database, "
              "and no creature is carrying a DM prop.")
        return
    print(f"{len(live)} DM quest(s) in the world database:")
    for quest in live:
        print(f"  {quest['id']}  {quest['title']}")
    if seeded:
        print(f"\n{len(seeded)} seeded prop(s) in creature loot:")
        for row in seeded:
            print(f"  creature loot {row['creature']}: item {row['item']}  [{row['tag']}]")
    print("\nPurging deletes each quest, its giver links, and every character's record of it,\n"
          "and takes every seeded prop back out of creature loot.\n"
          "The DM's memory (state.db) is kept, so the chronicle still reads correctly.")
    if not args.yes:
        if input("\ntype the word purge to go ahead: ").strip() != "purge":
            print("nothing was done.")
            return
    gone, failed = [], []
    for quest in live:
        try:
            apply_quest.run_sql(hot_quest.render_remove_by_id(quest["id"], quest["title"]))
            gone.append(quest["id"])
        except (apply_quest.StepFailed, hot_quest.SpecError) as error:
            failed.append((quest["id"], error))
        db.execute("UPDATE quests SET status = 'purged', retired_at = ? WHERE quest = ?",
                   (dm_state.now(), quest["id"]))
    db.commit()
    if gone:
        dm_state.add_event(db, 0, "purged", f"removed quests: {', '.join(str(i) for i in gone)}")
        db.commit()
        print(f"\nremoved {len(gone)} quest(s): {', '.join(str(i) for i in gone)}")
    for quest_id, error in failed:
        print(f"could not remove {quest_id}: {error}")

    # The quests stop asking first, then the props they seeded come out.
    reloads = ["reload all_quest"]
    if seeded:
        try:
            apply_quest.run_sql(hot_quest.render_props_purge())
            dm_state.add_event(db, 0, "purged", f"removed {len(seeded)} seeded loot row(s)")
            db.commit()
            print(f"removed {len(seeded)} seeded prop(s) from creature loot")
            reloads.append("reload creature_loot_template")
        except apply_quest.StepFailed as error:
            print(f"could not remove the seeded props: {error}")
            failed.append(("seeded props", error))
    for command in reloads:
        try:
            print(f"console: {command} -> {console.run(command) or 'ok'}")
        except console.ConsoleError as error:
            print(f"the database is clean but the server was not told ({error}).\n"
                  f"  Run `.{command}` in game.")
    print("Anyone holding one of these should relog.")
    if failed:
        sys.exit(1)


def whose_story(db, name, live_guid=None):
    """The state row for a name, and how many characters have ever held it.

    A character deleted and made again keeps its name but takes a new guid, so
    state can hold more than one row under one name. Prefer whoever holds the
    name in the game now; otherwise the most recently seen, because a
    chronicle outlives the character it is about.
    """
    held = db.execute("SELECT * FROM characters WHERE name = ? COLLATE NOCASE ORDER BY last_seen DESC",
                      (name,)).fetchall()
    if live_guid is not None:
        for row in held:
            if row["guid"] == live_guid:
                return row, len(held)
    return (held[0] if held else None), len(held)


def cmd_story(db, args):
    try:
        live = world_query.character(args.character)
    except world_query.QueryError:
        live = None
    row, held = whose_story(db, args.character, live["guid"] if live else None)
    if not row:
        sys.exit(f"dm: the Overseer has not noticed anyone called {args.character}")
    if held > 1:
        print(f"note: {held} characters have been called {row['name']}; "
              f"showing the one with guid {row['guid']}.\n")
    print(f"=== {row['name']}: first noticed {dm_state.ago(row['first_seen'])} ago ===\n")
    print("Story so far:\n  " + (row["story_so_far"] or "(nothing written yet)"))
    print("\nBounties:")
    quests = db.execute("SELECT * FROM quests WHERE guid = ? ORDER BY issued_at", (row["guid"],)).fetchall()
    for quest in quests:
        print(f"  {quest['quest']}  \"{quest['title']}\"  [{quest['status']}]  posted {dm_state.ago(quest['issued_at'])} ago")
        print(f"         {quest['story_beat']}")
        if quest["circumstances"]:
            print(f"         How it ended: {quest['circumstances']}")
    if not quests:
        print("  (none)")
    print("\nChronicle:")
    for event in db.execute("SELECT * FROM events WHERE guid = ? ORDER BY ts, id", (row["guid"],)):
        print(f"  {time.strftime('%Y-%m-%d %H:%M', time.localtime(event['ts']))}  {event['text'].split(' [')[0]}")


def cmd_context(db, args):
    """Print exactly what the model would be told about a character. No model call."""
    who = world_query.character(args.character)
    if not who:
        sys.exit(f"dm: no character named {args.character}")
    if not dm_state.get_character(db, who["guid"]):
        sys.exit("dm: the Overseer has not noticed this character yet; run a tick while they are online")
    try:
        context = write_quest.gather(args.character, skip=recent_targets(db, who["guid"]),
                                     props_in_use=props_in_use(db))
    except write_quest.NoContext as error:
        sys.exit(f"dm: {error}")
    print(write_quest.user_message(context, None, story=story_section(db, context["character"])))


def cmd_tick(db, _args):
    tick(db)
    db.commit()


def cmd_run(db, args):
    print(f"ticking every {args.every} seconds; Ctrl+C to stop")
    try:
        while True:
            try:
                tick(db)
                db.commit()
            except world_query.QueryError as error:
                print(f"tick failed: {error}")
            time.sleep(args.every)
    except KeyboardInterrupt:
        print("\nstopped.")


def main():
    parser = argparse.ArgumentParser(description="The Overseer's loop.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tick", help="one pass: observe, record, maybe propose").set_defaults(run=cmd_tick)
    loop = commands.add_parser("run", help="keep ticking")
    loop.add_argument("--every", type=int, default=300, help="seconds between ticks (default 300)")
    loop.set_defaults(run=cmd_run)
    commands.add_parser("pending", help="list proposals waiting for approval").set_defaults(run=cmd_pending)
    approve = commands.add_parser("approve", help="put a proposal live")
    approve.add_argument("number", type=int)
    approve.set_defaults(run=cmd_approve)
    reject = commands.add_parser("reject", help="discard a proposal")
    reject.add_argument("number", type=int)
    reject.add_argument("reason", nargs="?", default="", help="passed to the model next time")
    reject.set_defaults(run=cmd_reject)
    story = commands.add_parser("story", help="the chronicle for one character")
    story.add_argument("character")
    story.set_defaults(run=cmd_story)
    arc = commands.add_parser("arc", help="show a character's private arc, or reseed it")
    arc.add_argument("character")
    arc.add_argument("--seed", metavar="TEXT", help="a direction; a new arc is written around it")
    arc.set_defaults(run=cmd_arc)
    pause = commands.add_parser("pause", help="stop everything reaching the game")
    pause.add_argument("reason", nargs="?", default="", help="noted in the chronicle and shown on every refusal")
    pause.set_defaults(run=cmd_pause)
    commands.add_parser("resume", help="undo pause").set_defaults(run=cmd_resume)
    purge = commands.add_parser("purge", help="remove every DM quest from the game")
    purge.add_argument("--yes", action="store_true", help="skip the confirmation")
    purge.set_defaults(run=cmd_purge)
    context = commands.add_parser("context", help="show what the model would be told; no model call")
    context.add_argument("character")
    context.set_defaults(run=cmd_context)
    args = parser.parse_args()

    console.load_env()
    db = dm_state.connect()
    try:
        args.run(db, args)
    except world_query.QueryError as error:
        sys.exit(f"dm: {error}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
