#!/usr/bin/env python3
"""The Overseer's loop: watch the players, remember, and propose what comes next.

    python3 dm.py tick                 one pass: observe, record, maybe propose
    python3 dm.py run --every 300      keep ticking every 300 seconds
    python3 dm.py pending              list proposals waiting for you
    python3 dm.py approve 3            put proposal 3 live
    python3 dm.py reject 3 "too easy"  discard it, with a reason the story keeps
    python3 dm.py story Zachadin       the chronicle for one character
    python3 dm.py context Zachadin     what the model would be told next; no model call

A tick never puts a quest live. It only:
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
    DM_IGNORE_CHARACTERS    names the DM should not track, comma separated
    DM_MAIL_CHARACTER       name of a character the DM owns; mail sent to it is read as
                            letters to the Overseer (optional)
"""
import argparse
import copy
import json
import os
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
- story_beat: one sentence, past tense, the Overseer's view of what this bounty adds to the story.
- story_so_far: rewrite the running summary in at most 120 words: who this character is becoming in the Overseer's eyes, what has happened, and one or two open threads. It is your only memory next time, so keep what matters and drop what does not."""

SYSTEM = write_quest.RULES + CONTINUITY + write_quest.CLOSING

TOOL = copy.deepcopy(write_quest.TOOL)
TOOL["input_schema"]["properties"]["story_beat"] = {
    "type": "string", "description": "One sentence, past tense: what this bounty adds to the story."}
TOOL["input_schema"]["properties"]["story_so_far"] = {
    "type": "string", "description": "The rewritten running summary, at most 120 words."}
TOOL["input_schema"]["required"] += ["story_beat", "story_so_far"]


def setting(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def ignored():
    names = os.environ.get("DM_IGNORE_CHARACTERS", "") + "," + os.environ.get("DM_MAIL_CHARACTER", "")
    return {part.strip().lower() for part in names.split(",") if part.strip()}


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
            hunt = f"{spec['kill'][0]['count']} x {quest['target']}" if spec["kill"] else "no hunt"
            outcome = {
                "completed": f"turned in after {dm_state.ago(quest['issued_at'], quest['completed_at'])}",
                "accepted": "accepted, not finished yet",
                "offered": "posted, not yet accepted",
                "ignored": "ignored; it expired unaccepted",
            }[quest["status"]]
            lines.append(f"- \"{quest['title']}\" ({hunt}), posted {dm_state.ago(quest['issued_at'])} ago: {outcome}. "
                         f"Beat: {quest['story_beat']}")
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

    rejected = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                          "ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if rejected:
        lines += ["", "The server owner turned down your recent ideas for this character, saying:"]
        lines += [f"- {row['reason']}" for row in rejected]
    return "\n".join(lines)


def wants_bounty(db, who):
    """Why this character should not get a proposal right now, or None if it should."""
    if dm_state.pending_proposal(db, who["guid"]):
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


def propose(db, who, say):
    context = write_quest.gather(who["name"])
    message = write_quest.user_message(context, None, story=story_section(db, context["character"]))
    answer, usage = llm.ask_for_tool_call(SYSTEM, message, TOOL, max_tokens=4096)
    spec, target, announcement = write_quest.build_spec(answer, context, hot_quest.QUEST_ID_RANGE[0])
    beat = " ".join(str(answer.get("story_beat", "")).split())
    summary = " ".join(str(answer.get("story_so_far", "")).split())
    if not beat or not summary:
        raise write_quest.Rejected("the model left out the story beat or the story summary")
    if len(summary.split()) > 160:
        raise write_quest.Rejected("the story summary is far over 120 words")
    db.execute("INSERT INTO proposals (ts, guid, name, spec, target, announcement, dm_note, story_beat, story_so_far, "
               "model, tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(spec), json.dumps(target), announcement,
                answer.get("dm_note", ""), beat, summary, usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: proposal {number} written, \"{spec['title']}\". Review with: python3 dm.py pending")


def tick(db, say=print):
    try:
        console.run("saveall")
    except console.ConsoleError as error:
        say(f"note: could not refresh positions ({error}); using the last save")

    skip = ignored()
    players = [who for who in world_query.online_characters() if who["name"].lower() not in skip]
    say(f"tick: {len(players)} character(s) online")
    parties = world_query.parties()
    progress = world_query.dm_quest_progress()
    for who in players:
        observe_character(db, who, [m for m in parties.get(who["guid"], []) if m["name"].lower() not in skip], say)
    observe_company(db, players, parties, progress)
    observe_bounties(db, players, progress, say)
    observe_letters(db, say)
    db.commit()

    for who in players:
        reason = wants_bounty(db, who)
        if reason:
            say(f"  {who['name']}: no proposal ({reason})")
            continue
        if dm_state.proposals_in_last_hour(db) >= setting("DM_MAX_PROPOSALS_HOUR", 6):
            say("  hourly ceiling on model calls reached; no more proposals this tick")
            break
        try:
            propose(db, who, say)
        except write_quest.NoContext as error:
            say(f"  {who['name']}: no proposal ({error})")
        except (write_quest.Rejected, hot_quest.SpecError) as error:
            say(f"  {who['name']}: the model's bounty was rejected ({error}); it will try again next tick")
        except llm.LLMError as error:
            say(f"  {who['name']}: model call failed ({error})")
        db.commit()


# ---- commands ---------------------------------------------------------------

def show_proposal(row):
    spec, target = json.loads(row["spec"]), json.loads(row["target"])
    reward = [f"{spec['reward']['money_copper']} copper"] + [f"item {i['item']}" for i in spec["reward"]["items"]]
    print(f"""
=== Proposal {row['id']} for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===
Title:    {spec['title']}
Hunt:     {spec['kill'][0]['count']} x {target['name']} ({target['alive']} alive when written)
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
Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]""")


def cmd_pending(db, _args):
    found = db.execute("SELECT * FROM proposals WHERE status = 'pending' ORDER BY id").fetchall()
    if not found:
        print("no proposals waiting.")
    for row in found:
        show_proposal(row)
    if found:
        print("\napprove with: python3 dm.py approve <number>    reject with: python3 dm.py reject <number> \"why\"")


def cmd_approve(db, args):
    row = db.execute("SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (args.number,)).fetchone()
    if not row:
        sys.exit(f"dm: no pending proposal {args.number}")
    spec = json.loads(row["spec"])
    spec["id"] = world_query.next_quest_id()        # the id is fixed only now, so it cannot collide
    try:
        apply_quest.apply_spec(spec, announce=row["announcement"])
    except (apply_quest.StepFailed, hot_quest.SpecError) as error:
        sys.exit(f"dm: not applied: {error}")
    except console.ConsoleError as error:
        print(f"warning: the quest is in the database but the console step failed: {error}\n"
              f"  Run `.reload all_quest` in game to load it.")
    stamp = dm_state.now()
    db.execute("INSERT INTO quests (quest, guid, title, target, spec, announcement, dm_note, story_beat, model, "
               "issued_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (spec["id"], row["guid"], spec["title"], json.loads(row["target"])["name"], json.dumps(spec),
                row["announcement"], row["dm_note"], row["story_beat"], row["model"], stamp))
    db.execute("UPDATE characters SET story_so_far = ? WHERE guid = ?", (row["story_so_far"], row["guid"]))
    db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
    dm_state.add_event(db, row["guid"], "overseer", f"the Overseer posted the bounty \"{spec['title']}\"")
    db.commit()
    print(f"quest {spec['id']} is live for {row['name']}; the story has moved on.")


def cmd_reject(db, args):
    row = db.execute("SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (args.number,)).fetchone()
    if not row:
        sys.exit(f"dm: no pending proposal {args.number}")
    db.execute("UPDATE proposals SET status = 'rejected', decided_at = ?, reason = ? WHERE id = ?",
               (dm_state.now(), args.reason or "", row["id"]))
    db.commit()
    print(f"proposal {row['id']} rejected. A new one will be written on the next tick"
          + (", with your reason passed to the model." if args.reason else "."))


def cmd_story(db, args):
    row = db.execute("SELECT * FROM characters WHERE name = ? COLLATE NOCASE", (args.character,)).fetchone()
    if not row:
        sys.exit(f"dm: the Overseer has not noticed anyone called {args.character}")
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
    try:
        context = write_quest.gather(args.character)
    except write_quest.NoContext as error:
        sys.exit(f"dm: {error}")
    if not dm_state.get_character(db, context["character"]["guid"]):
        sys.exit("dm: the Overseer has not noticed this character yet; run a tick while they are online")
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
