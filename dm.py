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
    python3 dm.py campaign Zachadin    what the Overseer has decided a character's life is about (a spoiler)
    python3 dm.py campaign Zachadin --seed "a priest who will lose her faith and find another"
    python3 dm.py chapter Zachadin     the Overseer's zone chapters for a character (a spoiler)
    python3 dm.py dossier 40           a zone's own story, from its stock quests (--side, --refresh)
    python3 dm.py context Zachadin     what the model would be told next; no model call
    python3 dm.py voices               every herald's settled voice note
    python3 dm.py voice McBride        one herald's facts, own lines and note
    python3 dm.py voice 197 "text"     write or replace a note, and pin it
    python3 dm.py voice 197 --forget   clear it; the next bounty through them settles a new one
    python3 dm.py canon                every fact the story has established
    python3 dm.py canon Zachadin       one character's facts, and the server-wide ones
    python3 dm.py canon --add "text"   state a fact yourself (--zone, --creature to tag it)
    python3 dm.py canon --retire 12    withdraw a fact; no prompt is shown it again
    python3 dm.py pause "why"          the kill switch: nothing reaches the game
    python3 dm.py resume               undo it
    python3 dm.py purge                take every DM quest back out of the game

One bounty at a time: a character with a bounty offered or accepted gets no new proposal.
An accepted bounty never expires; one that is never accepted is dropped after DM_STALE_HOURS.
When a character is first noticed, the Overseer writes their campaign: a premise, the one question
their story asks, and three to five acts by level band, each an intent rather than an event. When
their level leaves an act's band, the act is reviewed and what remains may be rewritten.
The campaign's current act is played out in zone chapters. Once they settle in a zone (the same zone for
DM_SETTLE_TICKS ticks, not a capital, with enough stock quests), the Overseer writes a chapter
for them there, grounded in that zone's own quests. Settling elsewhere pauses it; coming back
resumes it. A mini-arc is planned from the chapter's next seed, and every bounty serves the arc
in force. A mini-arc covers a few levels. It ends when its finale is turned in, the character
outgrows it, or they settle in another zone, and the next one is planned from how it ended.
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
                            bounty, arc, chapter, campaign, act_review, letter (default: letter)
    DM_SETTLE_TICKS         consecutive ticks in one zone before a character counts as settled there
                            (default 3)
    DM_SEALED               plan types you do not want to read, comma separated: campaign,
                            act_review, chapter, arc.
                            They are approved without review, and their text is printed only
                            with --reveal. For an owner who plays on the server
    DM_CHARACTERS           if set, the only characters the DM notices, comma separated.
                            Set this on a playerbot realm, where a thousand characters
                            exist and only a few of them are people
    DM_IGNORE_CHARACTERS    names the DM should not track, comma separated
    DM_MAIL_CHARACTER       name of a character the DM owns; mail sent to it is read as
                            letters to the Overseer (optional)
    DM_GEAR_EVERY           at most one gear reward in this many consecutive bounties (default 2)
    DM_PRIZE_LEVEL_SPAN     levels a character must gain between prizes (default 4)
    DM_PRIZE_REACH          how many levels above the character a prize may be (default 5)
    DM_PAUSE                set to 1 to pause without the flag file (`pause` writes the file)
    DM_PAUSE_FILE           where the flag file lives (default: `paused` beside this script)

Paused means paused: no tick, no model call, no quest written and no console
command sent, whichever script is run. Reading is unaffected, so `story`,
`arc`, `context` and `pending` still answer.

Sealed means unread: a sealed plan goes into force without review, and every
command that would print it (`pending`, `campaign`, `arc`, `chapter`,
`context`, `canon` and the tick's own log) says only that it exists, unless given --reveal.
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
- A private campaign and zone chapter may be shown above the arc. They set direction, not wording: the bounty is still one concrete errand in the herald's own voice. Never summarise the plan, name an act or a reveal, or have a herald speak as if they know the Overseer's design.
- Established facts are shown below when there are any. Never contradict one. A fact about another character is true in their story too: build on it when it fits, but do not take it over.
- canon_add: up to two facts this bounty establishes, each one sentence of at most 25 words, in the world's terms. Facts, not plans: "Deputy Willem knows the kobolds were paid", never "the Overseer intends". Leave it empty when the bounty establishes nothing new.
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
CANON_ADD = {
    "type": "array", "maxItems": 2, "items": {"type": "string"},
    "description": "Up to two facts this establishes, one sentence of at most 25 words each. Facts, not plans."}
TOOL["input_schema"]["properties"]["canon_add"] = CANON_ADD
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
- If a zone chapter is shown, the arc plays out the seed it names, in that place. Its adversary must be one of the creatures the zone's dossier lists among its enemies, or the chapter's finale creature: give that creature's id as adversary_creature, and name the group it stands for as adversary. With no chapter, adversary_creature is null.
- Established facts are shown when there are any. Never contradict one.
- canon_add: up to two facts this arc's premise takes as true about the world, one sentence of at most 25 words each. Facts, not plans: "the Defias are paid from inside Stormwind", never "the character will learn". Usually empty.

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
            "adversary_creature": {"type": ["integer", "null"],
                                   "description": "With a chapter: the id of a creature from the dossier's enemies, "
                                                  "or the chapter's finale creature, that stands for the adversary. "
                                                  "Otherwise null."},
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
            "canon_add": CANON_ADD,
        },
        "required": ["premise", "lure", "adversary", "beats", "signature_reward", "previous_outcome", "dm_note"],
    },
}

ACT_FIELDS = {
    "type": "object",
    "properties": {
        "level_band": {"type": "string", "description": "Levels this act covers, like 10-25."},
        "intent": {"type": "string", "description": "One sentence: what changes for the character. Never an event."},
        "thread": {"type": "string", "description": "The thread of Azeroth's story in 1.12 this act leans on."},
        "zones": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                  "description": "Where that thread runs for this character's faction."},
    },
    "required": ["level_band", "intent", "thread", "zones"],
}
REVEAL_FIELDS = {
    "type": "object",
    "properties": {"act": {"type": "integer", "description": "The act it belongs to, by its number."},
                   "text": {"type": "string", "description": "One sentence: what is learned."}},
    "required": ["act", "text"],
}

CAMPAIGN_SYSTEM = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You are deciding in secret what one character's whole life on this server is about.

Write a campaign: the spine of their story from now to level 60. It is a spine, not a script. The player chooses where to go and what to do; the zone chapters and mini-arcs written later turn it into events where the character actually is.

- premise: two sentences on what the Overseer sees in this character.
- question: the one question the story asks of them, such as "Will the hunter become what he hunts?"
- stake: one sentence on what the Overseer itself wants from the answer.
- acts: three to five, in order and without gaps between their level bands. The first contains the character's current level and the last ends at 60. An act's intent is one sentence on what changes for the character, never an event: "he learns the bandits are paid from inside the city", not "he kills VanCleef". thread names the thread of Azeroth's story as it stands in 1.12 that the act leans on, and zones lists where that thread runs for this character's faction. Do not assume the character will go there.
- cast: three to six key players, each a real figure or faction of that era, with their role in this story.
- reveals: two or three things to be learned, each pinned to an act by its number, not to an event.
- If the character has history, the campaign starts from it: what has happened is the opening of the story, not something to overwrite.
- One line on each other character's campaign is shown when there are any. Their stories may touch this one; do not copy them.
- If the server owner gives a direction, build the campaign around it.
- Established facts are shown when there are any. Never contradict one.
- canon_add: up to two facts this campaign takes as true about the world, one sentence of at most 25 words each. Facts, not plans. Usually empty.

This plan is never shown to the player.

Respond by calling the submit_campaign tool exactly once. Do not reply with prose."""

CAMPAIGN_TOOL = {
    "name": "submit_campaign",
    "description": "Submit the private campaign for this character.",
    "input_schema": {
        "type": "object",
        "properties": {
            "premise": {"type": "string", "description": "Two sentences."},
            "question": {"type": "string", "description": "The one question the story asks of them."},
            "stake": {"type": "string", "description": "What the Overseer wants from the answer."},
            "acts": {"type": "array", "items": ACT_FIELDS, "minItems": 3, "maxItems": 5},
            "cast": {"type": "array", "minItems": 3, "maxItems": 6,
                     "items": {"type": "object",
                               "properties": {"name": {"type": "string"},
                                              "role": {"type": "string", "description": "Their part in this story."}},
                               "required": ["name", "role"]}},
            "reveals": {"type": "array", "items": REVEAL_FIELDS, "minItems": 2, "maxItems": 3},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this story for this character."},
            "canon_add": CANON_ADD,
        },
        "required": ["premise", "question", "stake", "acts", "cast", "reveals", "dm_note"],
    },
}

ACT_REVIEW_SYSTEM = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. A character has just levelled out of the current act of the campaign you wrote for them. Review it in secret.

- outcome: one or two sentences on how the act actually ended, judged from the record and not from how it was planned.
- remaining_acts: the acts still to come. Rewrite them if what happened calls for it; otherwise repeat them unchanged. The rules are the same as when the campaign was written: in order and without gaps between level bands, the first containing the character's current level and the last ending at 60; intents, never events. If the character has ignored the story, the campaign may become about that.
- reveals: the reveals still to come, zero to three, each pinned to one of the remaining acts by its number in the whole campaign. The number of the first remaining act is given.
- The premise, question, stake and cast stand.
- Established facts are shown when there are any. Never contradict one.
- canon_add: up to two facts the act established, one sentence of at most 25 words each. Facts, not plans.

This plan is never shown to the player.

Respond by calling the submit_act_review tool exactly once. Do not reply with prose."""

ACT_REVIEW_TOOL = {
    "name": "submit_act_review",
    "description": "Submit the review of the act that has just ended.",
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "description": "How the act actually ended, in one or two sentences."},
            "remaining_acts": {"type": "array", "items": ACT_FIELDS, "minItems": 1, "maxItems": 4},
            "reveals": {"type": "array", "items": REVEAL_FIELDS, "maxItems": 3},
            "dm_note": {"type": "string", "description": "One sentence for the log."},
            "canon_add": CANON_ADD,
        },
        "required": ["outcome", "remaining_acts", "reveals", "dm_note"],
    },
}

MAX_LEVEL = 60
CAMPAIGN_WORDS = 70     # the campaign block in a bounty prompt

CHAPTER_SYSTEM = """You are the Overseer, an unseen dungeon master for a small private World of Warcraft (1.12) server with one to three players. You are deciding in secret how one character's story plays out in the zone they have settled in.

Write a zone chapter: the stretch of their story that happens in this place. Mini-arcs, each a few levels long, are planned from its seeds in turn, and bounties from those.

- premise: two or three sentences on how the character's story plays out here. Ground it in the zone's own story as its quests tell it, and follow the direction given: the campaign's current act when there is one, otherwise how their story stands. A reveal due in the current act may land here if this place suits it.
- local_cast: up to four creature ids from the dossier's list of who gives quests: the people this chapter speaks through. They are favoured as heralds when they are near.
- adversary: the local adversary, named. adversary_creature: the id of one creature from the dossier's enemies that stands for it.
- seeds: two to four one-line ideas for mini-arcs, in order, each one a step the next can build on. Say what a stretch is about, not what the character will do: the player chooses that.
- finale: one sentence on what ends the chapter, usually a named creature from the enemies or the zone's dungeon. finale_creature: its id when it is on the enemies list, otherwise null.
- hooks: one sentence on where the story could go after this place.
- Use the zone's real figures and factions as they are in 1.12, and keep them where the dossier places them.
- If a chapter in this zone has finished before, this one is its sequel and must follow from how that one ended.
- Established facts are shown when there are any. Never contradict one.
- canon_add: up to two facts this chapter takes as true about the world, one sentence of at most 25 words each. Facts, not plans. Usually empty.

This plan is never shown to the player.

Respond by calling the submit_chapter tool exactly once. Do not reply with prose."""

CHAPTER_TOOL = {
    "name": "submit_chapter",
    "description": "Submit the private zone chapter for this character.",
    "input_schema": {
        "type": "object",
        "properties": {
            "premise": {"type": "string", "description": "Two or three sentences."},
            "local_cast": {"type": "array", "items": {"type": "integer"}, "maxItems": 4,
                           "description": "Creature ids from the dossier's quest givers."},
            "adversary": {"type": "string", "description": "The local adversary, named."},
            "adversary_creature": {"type": ["integer", "null"],
                                   "description": "Id of a creature from the dossier's enemies that stands for it."},
            "seeds": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4,
                      "description": "One-line ideas for mini-arcs, in order."},
            "finale": {"type": "string", "description": "One sentence: what ends the chapter."},
            "finale_creature": {"type": ["integer", "null"],
                                "description": "Id of the finale creature from the dossier's enemies, or null."},
            "hooks": {"type": "string", "description": "One sentence: where the story could go next."},
            "dm_note": {"type": "string", "description": "One sentence for the log: why this chapter for this character."},
            "canon_add": CANON_ADD,
        },
        "required": ["premise", "local_cast", "adversary", "adversary_creature", "seeds", "finale",
                     "finale_creature", "hooks", "dm_note"],
    },
}

CHAPTER_WORDS = 80      # the chapter block in a bounty prompt

ARC_REACH = 10          # an arc's bands and its signature reward stay within this many levels of the character

BEAT_STATE = ("done", "current", "later")
CANON_PER_ANSWER = 2    # facts one piece of writing may establish


class ApproveFailed(Exception):
    pass


def auto_approved():
    """Proposal types that go live without waiting for a person. A sealed type is one of them."""
    found = {part.strip().lower() for part in os.environ.get("DM_AUTO_APPROVE", "letter").split(",") if part.strip()}
    return found | sealed()


def sealed():
    """Plan types the owner has chosen not to read: approved without review, and printed only with --reveal."""
    return {part.strip().lower() for part in os.environ.get("DM_SEALED", "").split(",") if part.strip()}


def arc_called(arc):
    """'the arc against the Scourge', or just 'the arc' when arcs are sealed."""
    return "the arc" if "arc" in sealed() else f"the arc against {arc['adversary']}"


def plan_note(kind, text):
    """' (adversary: X)' for the log, or '' when plans of this kind are sealed."""
    return "" if kind in sealed() else f" ({text})"


def fact_sealed(row, shown):
    """Whether a canon fact came from a plan whose kind is sealed and not revealed."""
    kind = (row["source"] or "").split(" ")[0]
    return kind in sealed() - shown


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
                dm_state.set_reward_status(db, quest["quest"], "collected")
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
                say(f"  {row['name']}: {arc_called(finale)} has ended; a new one will be planned")
            chapter = dm_state.active_chapter(db, quest["guid"])
            if chapter and chapter["finale_creature"] is not None:
                try:
                    hunted = bounty_creatures(json.loads(quest["spec"] or "{}"))
                except json.JSONDecodeError:
                    hunted = set()
                if chapter["finale_creature"] in hunted:
                    finish_chapter(db, chapter, "finale", say, row["name"])

    stale = dm_state.now() - int(setting("DM_STALE_HOURS", 24) * 3600)
    for quest in db.execute("SELECT * FROM quests WHERE status = 'offered' AND issued_at < ?", (stale,)).fetchall():
        db.execute("UPDATE quests SET status = 'ignored' WHERE quest = ?", (quest["quest"],))
        dm_state.set_reward_status(db, quest["quest"], "lapsed")        # frees the gear budget it used
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

def canon_facts(answer):
    """The facts a model answer wants established, cleaned. Empty or overlong ones are dropped, not fatal."""
    found = answer.get("canon_add") or []
    if not isinstance(found, list):
        return []
    facts = [dm_state.clean_fact(fact) for fact in found]
    return [fact for fact in facts if fact][:CANON_PER_ANSWER]


def canon_lines(db, guid, zone=None, creatures=()):
    """The established facts a prompt is shown, newest first, or [] when there are none."""
    found = dm_state.canon_for(db, guid, zone, creatures)
    if not found:
        return []
    return ["Established facts (never contradict them):"] + [f"- {row['fact']}" for row in found]


def establish(db, guid, zone, creature, facts, source):
    """Write an approved proposal's facts to canon."""
    for fact in facts or []:
        dm_state.add_canon(db, guid, zone, creature, fact, source)


def story_section(db, who, givers=(), hidden=()):
    """Everything the model needs to carry the story forward for one character.

    givers is the candidate herald list; facts about any of them are shown.
    hidden is plan kinds to leave out, for printing to an owner who has sealed
    them; the model is always shown everything.
    """
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
            through = parties_text(quest)
            lines.append(f"- \"{quest['title']}\" ({hunt}){through}, posted {dm_state.ago(quest['issued_at'])} ago: "
                         f"{outcome}.{paid_text(db, quest['quest'])} Beat: {quest['story_beat']}")
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
    if saga and "arc" not in hidden:
        lines += ["", "Earlier arcs, now ended, oldest first:"] + saga
    campaign = campaign_block(db, who["guid"])
    if campaign:
        lines += ["", "[the campaign is sealed]" if "campaign" in hidden else campaign]
    chapter = chapter_block(db, who["guid"])
    if chapter:
        lines += ["", "[the chapter in force is sealed]" if "chapter" in hidden else chapter]
    arc = dm_state.active_arc(db, who["guid"])
    if arc:
        lines += ["", "[the arc in force is sealed]" if "arc" in hidden
                  else arc_text(arc, "Your private arc for this character (never state it to the player):")]
    facts = canon_lines(db, who["guid"], who.get("zone"), [g["creature"] for g in givers])
    if facts and hidden:
        found = dm_state.canon_for(db, who["guid"], who.get("zone"), [g["creature"] for g in givers])
        kept = [row for row in found if (row["source"] or "").split(" ")[0] not in hidden]
        facts = ["Established facts (never contradict them):"] + [f"- {row['fact']}" for row in kept]
        if len(kept) < len(found):
            facts.append(f"- [{len(found) - len(kept)} more, from sealed plans]")
    if facts:
        lines += [""] + facts

    rejected = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                          "AND type = 'bounty' ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if rejected:
        lines += ["", "The server owner turned down your recent ideas for this character, saying:"]
        lines += [f"- {row['reason']}" for row in rejected]
    return "\n".join(lines)


def paid_text(db, quest):
    """' It paid the prize Smite's Mighty Hammer.' for the story, or '' when it paid only coin."""
    reward = dm_state.reward_of(db, quest)
    if not reward:
        return ""
    names = json.loads(reward["names"] or "[]")
    what = {"standard": "gear", "prize": "the prize", "capstone": "the book"}.get(reward["tier"], reward["tier"])
    choice = "a choice of " if len(names) > 1 else ""
    return f" It paid {choice}{what} {' or '.join(names)}."


def gear_tiers(db, who):
    """The gear tiers the budget allows this character's next bounty."""
    return dm_state.gear_budget(db, who["guid"], who["level"], every=setting("DM_GEAR_EVERY", 2),
                                span=setting("DM_PRIZE_LEVEL_SPAN", 4))


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


def validate_arc(answer, book_names, level=None, enemies=None):
    """Check the model's arc and return it in stored form. Raises write_quest.Rejected.

    enemies: with a chapter, the creature ids its adversary may be; None when the arc has no chapter.
    """
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
    creature = answer.get("adversary_creature")
    if enemies is not None and (not isinstance(creature, int) or creature not in enemies):
        raise write_quest.Rejected(f"arc adversary creature {creature} is not among the zone's enemies "
                                   "or the chapter's finale")
    arc["adversary_creature"] = creature if isinstance(creature, int) else None
    arc["previous_outcome"] = " ".join(str(answer.get("previous_outcome", "")).split())[:500]
    arc["dm_note"] = " ".join(str(answer.get("dm_note", "")).split())
    return arc


def saga_lines(db, guid):
    """Earlier arcs as short lines: what was planned and how each ended."""
    lines = []
    for arc in dm_state.past_arcs(db, guid):
        ended = {"resolved": "its finale was turned in", "outgrown": "the character outgrew it unfinished",
                 "replaced": "the server owner replaced it",
                 "left": "the character left for another zone"}.get(arc["end_reason"], "it ended")
        lines.append(f"- Against {arc['adversary']}: {arc['lure']} Ended: {ended}."
                     + (f" Outcome: {arc['outcome']}" if arc["outcome"] else ""))
    return lines


def arc_message(db, who, books, seed, chapter=None, dossier=None, seed_index=None):
    known = dm_state.get_character(db, who["guid"])
    lines = [f"Character: {world_query.describe(who)}, currently in {world_query.zone_name(who['zone'])}.", ""]
    campaign = campaign_block(db, who["guid"])
    if campaign:
        lines += [campaign, ""]
    if chapter:
        lines += [chapter_text(chapter, "The zone chapter this arc belongs to:"), ""]
        if seed_index is not None:
            lines += [f"Build this arc from seed {seed_index + 1}: {json.loads(chapter['seeds'])[seed_index]}", ""]
        if dossier:
            lines += ["The zone's own story, from its stock quests:", world_query.dossier_text(dossier), ""]

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
    facts = canon_lines(db, who["guid"], who.get("zone"))
    if facts:
        lines += [""] + facts
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


def propose_arc(db, who, seed, say, chapter=None):
    """Ask the model for an arc and store it as a proposal. Returns the proposal number.

    With a chapter, the arc is planned from its next seed (unless the owner gave a direction
    of their own) and its adversary must come from the chapter's zone.
    """
    books = world_query.reward_items(who, reach=ARC_REACH, limit=40)
    dossier = enemies = seed_index = None
    if chapter:
        dossier = dossier_for(db, chapter["zone"], world_query.side(who["race"]))
        enemies = allowed_adversaries(chapter, dossier)
        if not seed and seeds_left(chapter):
            seed_index = chapter["current_seed"]
    message = arc_message(db, who, books, seed, chapter, dossier, seed_index)
    answer, usage = llm.ask_for_tool_call(ARC_SYSTEM, message, ARC_TOOL, max_tokens=4096)
    arc = validate_arc(answer, [book["name"] for book in books], who["level"], enemies)
    arc["seed"] = seed or ""
    arc["chapter"] = chapter["id"] if chapter else None
    arc["chapter_seed"] = seed_index
    arc["canon_add"] = canon_facts(answer)
    arc["zone"] = who.get("zone")
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, dm_note, model, tokens_in, tokens_out) "
               "VALUES (?, ?, ?, 'arc', ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(arc), arc["dm_note"], usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: arc proposal {number} written{plan_note('arc', 'adversary: ' + arc['adversary'])}")
    return number


def band(text):
    """(low, high) from a level band like '10-25'. Raises write_quest.Rejected."""
    text = "".join(str(text or "").split())
    if not re.fullmatch(r"\d{1,2}-\d{1,2}", text):
        raise write_quest.Rejected(f"level band {text!r} is not like 10-25")
    low, high = (int(part) for part in text.split("-"))
    if low > high or high > MAX_LEVEL:
        raise write_quest.Rejected(f"level band {text} runs backwards or past {MAX_LEVEL}")
    return low, high


def validate_acts(acts, level, fewest, most):
    """Acts in stored form: in order, without gaps, from the current level to 60. Raises write_quest.Rejected."""
    if not isinstance(acts, list) or not fewest <= len(acts) <= most:
        raise write_quest.Rejected(f"a campaign needs {fewest} to {most} acts here")
    found, previous = [], None
    for act in acts:
        if not isinstance(act, dict):
            raise write_quest.Rejected("each act needs a level band, an intent, a thread and zones")
        low, high = band(act.get("level_band"))
        if previous is not None and low not in (previous, previous + 1):
            raise write_quest.Rejected("acts must follow one another without gaps or overlaps")
        previous = high
        intent = " ".join(str(act.get("intent", "")).split())
        thread = " ".join(str(act.get("thread", "")).split())
        if not intent or len(intent) > 300 or not thread or len(thread) > 200:
            raise write_quest.Rejected("each act needs one sentence of intent and the thread it leans on")
        zones = act.get("zones") or []
        if not isinstance(zones, list):
            raise write_quest.Rejected("an act's zones are a list of names")
        found.append({"level_band": f"{low}-{high}", "intent": intent, "thread": thread,
                      "zones": [" ".join(str(z).split())[:40] for z in zones[:6]]})
    first, last = band(found[0]["level_band"]), band(found[-1]["level_band"])
    if not first[0] <= level <= first[1]:
        raise write_quest.Rejected(f"the first act must contain the character's level, {level}")
    if last[1] != MAX_LEVEL:
        raise write_quest.Rejected(f"the last act must end at {MAX_LEVEL}")
    return found


def validate_reveals(reveals, first, last, fewest, most):
    """Reveals pinned to acts numbered first..last (1-based). Raises write_quest.Rejected."""
    if not isinstance(reveals, list) or not fewest <= len(reveals) <= most:
        raise write_quest.Rejected(f"give {fewest} to {most} reveals")
    found = []
    for reveal in reveals:
        act = reveal.get("act") if isinstance(reveal, dict) else None
        text = " ".join(str(reveal.get("text", "")).split()) if isinstance(reveal, dict) else ""
        if not isinstance(act, int) or not first <= act <= last or not text or len(text) > 300:
            raise write_quest.Rejected(f"each reveal is one sentence pinned to an act from {first} to {last}")
        found.append({"act": act, "text": text})
    return found


def validate_campaign(answer, level):
    """Check the model's campaign and return it in stored form. Raises write_quest.Rejected."""
    campaign = {}
    for field, limit in (("premise", 500), ("question", 200), ("stake", 300)):
        text = " ".join(str(answer.get(field, "")).split())
        if not text or len(text) > limit:
            raise write_quest.Rejected(f"campaign {field} is missing or over {limit} characters")
        campaign[field] = text
    campaign["acts"] = validate_acts(answer.get("acts"), level, 3, 5)
    cast = answer.get("cast")
    if not isinstance(cast, list) or not 3 <= len(cast) <= 6:
        raise write_quest.Rejected("a campaign needs three to six key players")
    campaign["cast"] = []
    for member in cast:
        name = " ".join(str(member.get("name", "")).split()) if isinstance(member, dict) else ""
        role = " ".join(str(member.get("role", "")).split()) if isinstance(member, dict) else ""
        if not name or len(name) > 80 or not role or len(role) > 200:
            raise write_quest.Rejected("each key player needs a name and a role")
        campaign["cast"].append({"name": name, "role": role})
    campaign["reveals"] = validate_reveals(answer.get("reveals"), 1, len(campaign["acts"]), 2, 3)
    campaign["dm_note"] = " ".join(str(answer.get("dm_note", "")).split())
    campaign["canon_add"] = canon_facts(answer)
    return campaign


def loads(value):
    return json.loads(value) if isinstance(value, str) else value


def campaign_text(campaign, heading):
    """A campaign as plain text: the spine, each act marked done, current or later, the cast and the reveals."""
    acts, current = loads(campaign["acts"]), campaign["current_act"]
    lines = [heading, f"Premise: {campaign['premise']}", f"Question: {campaign['question']}",
             f"Stake: {campaign['stake']}", "Acts:"]
    for index, act in enumerate(acts):
        state = BEAT_STATE[0] if index < current else BEAT_STATE[1] if index == current else BEAT_STATE[2]
        lines.append(f"{index + 1}. [{state}] levels {act['level_band']}: {act['intent']} "
                     f"(thread: {act['thread']}; zones: {', '.join(act['zones']) or 'any'})")
        if act.get("outcome"):
            lines.append(f"   Outcome: {act['outcome']}")
    lines.append("Key players: " + "; ".join(f"{m['name']}, {m['role']}" for m in loads(campaign["cast"])))
    lines.append("Reveals: " + "; ".join(f"act {r['act']}: {r['text']}" for r in loads(campaign["reveals"])))
    return "\n".join(lines)


def current_act_text(campaign):
    """The campaign's current act and the reveals due in it, for a chapter call."""
    acts, index = loads(campaign["acts"]), campaign["current_act"]
    act = acts[index]
    lines = [f"The campaign: {campaign['premise']} Its question: {campaign['question']}",
             f"Current act ({index + 1} of {len(acts)}, levels {act['level_band']}): {act['intent']} "
             f"Thread: {act['thread']}. Where it runs: {', '.join(act['zones']) or 'anywhere'}.",
             "Key players: " + "; ".join(f"{m['name']}, {m['role']}" for m in loads(campaign["cast"]))]
    due = [r["text"] for r in loads(campaign["reveals"]) if r["act"] == index + 1]
    if due:
        lines.append("Due to be learned in this act: " + " ".join(due))
    return "\n".join(lines)


def campaign_block(db, guid):
    """The campaign in force, as briefly as a bounty prompt can carry it, or None."""
    campaign = dm_state.active_campaign(db, guid)
    if not campaign:
        return None
    act = loads(campaign["acts"])[campaign["current_act"]]
    text = f"{campaign['premise']} Question: {campaign['question']} Now: {act['intent']}"
    return "Your private campaign for this character (never state it to the player): " + clip_words(text, CAMPAIGN_WORDS)


def act_left(campaign, level):
    """Has the character levelled out of the campaign's current act, with another act to go to?"""
    acts = loads(campaign["acts"])
    return campaign["current_act"] < len(acts) - 1 and level > band(acts[campaign["current_act"]]["level_band"])[1]


def campaign_message(db, who, books, seed):
    known = dm_state.get_character(db, who["guid"])
    lines = [f"Character: {world_query.describe(who)}, currently in {world_query.zone_name(who['zone'])}.", ""]
    events = dm_state.events_since(db, who["guid"], 0, limit=30)
    lines.append("What you know of them so far:")
    lines += [f"- {event['text'].split(' [')[0]}" for event in events] or ["- nothing yet"]
    if known and known["story_so_far"]:
        lines += ["", "Story so far (your own summary):", known["story_so_far"]]
    saga = saga_lines(db, who["guid"])
    if saga:
        lines += ["", "Arcs that have ended, oldest first:"] + saga
    arc = dm_state.active_arc(db, who["guid"])
    if arc:
        lines += ["", arc_text(arc, "The arc in force, which runs to its end:")]
    chapters = dm_state.chapters_of(db, who["guid"])
    if chapters:
        lines += ["", "Zone chapters so far:"]
        lines += [f"- {world_query.zone_name(c['zone'])} ({c['status']}): {c['premise']}" for c in chapters]
    others = dm_state.other_campaigns(db, who["guid"])
    if others:
        lines += ["", "Other characters' campaigns, one line each:"]
        lines += [f"- {row['name']}: {clip_words(row['premise'], 30)}" for row in others]
    lines += ["", "Capstone books this character's class can use (name, level needed):"]
    lines += [f"- {book['name']}, level {book['required_level']}" for book in books] or ["- none"]
    facts = canon_lines(db, who["guid"], who.get("zone"))
    if facts:
        lines += [""] + facts
    turned_down = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                             "AND type = 'campaign' ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if turned_down:
        lines += ["", "The server owner turned down your earlier campaigns for this character, saying:"]
        lines += [f"- {row['reason']}" for row in turned_down]
    if seed:
        lines += ["", f"Direction from the server owner: {seed}"]
    return "\n".join(lines)


def propose_campaign(db, who, seed, say):
    """Ask the model for a campaign and store it as a proposal. Returns the proposal number."""
    books = world_query.reward_items(who, reach=MAX_LEVEL, limit=40)
    answer, usage = llm.ask_for_tool_call(CAMPAIGN_SYSTEM, campaign_message(db, who, books, seed), CAMPAIGN_TOOL,
                                          max_tokens=4096, model=llm.plan_model())
    campaign = validate_campaign(answer, who["level"])
    campaign["seed"] = seed or ""
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, dm_note, model, tokens_in, tokens_out) "
               "VALUES (?, ?, ?, 'campaign', ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(campaign), campaign["dm_note"],
                usage.get("model"), usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: campaign proposal {number} written{plan_note('campaign', campaign['question'])}")
    return number


def act_review_message(db, who, campaign):
    index = campaign["current_act"]
    lines = [f"Character: {world_query.describe(who)}, currently in {world_query.zone_name(who['zone'])}.", "",
             campaign_text(campaign, "The campaign:"), "",
             f"Act {index + 1} has just ended. The first remaining act is act {index + 2}.", "",
             "What happened during it:"]
    since = campaign["updated_at"] or 0
    quests = db.execute("SELECT * FROM quests WHERE guid = ? AND issued_at >= ? ORDER BY issued_at",
                        (who["guid"], since)).fetchall()
    for quest in quests:
        lines.append(f"- Bounty \"{quest['title']}\": {quest['story_beat']} "
                     + (quest["circumstances"] or f"Status: {quest['status']}."))
    for arc in db.execute("SELECT * FROM arcs WHERE guid = ? AND ended_at >= ? ORDER BY id", (who["guid"], since)):
        lines.append(f"- An arc against {arc['adversary']} ended ({arc['end_reason']})"
                     + (f": {arc['outcome']}" if arc["outcome"] else "."))
    for chapter in db.execute("SELECT * FROM chapters WHERE guid = ? AND updated_at >= ? ORDER BY id",
                              (who["guid"], since)):
        lines.append(f"- The chapter in {world_query.zone_name(chapter['zone'])} is {chapter['status']}: "
                     f"{chapter['premise']}")
    events = [e for e in dm_state.events_since(db, who["guid"], since, limit=30) if e["kind"] != "overseer"]
    lines += [f"- {event['text'].split(' [')[0]}" for event in events]
    if len(lines) and lines[-1] == "What happened during it:":
        lines.append("- nothing the Overseer saw")
    known = dm_state.get_character(db, who["guid"])
    if known and known["story_so_far"]:
        lines += ["", "Story so far (your own summary):", known["story_so_far"]]
    facts = canon_lines(db, who["guid"], who.get("zone"))
    if facts:
        lines += [""] + facts
    return "\n".join(lines)


def propose_act_review(db, who, campaign, say):
    """Ask the model to review the act the character has levelled out of. Returns the proposal number."""
    answer, usage = llm.ask_for_tool_call(ACT_REVIEW_SYSTEM, act_review_message(db, who, campaign), ACT_REVIEW_TOOL,
                                          max_tokens=4096, model=llm.plan_model())
    index = campaign["current_act"]
    outcome = " ".join(str(answer.get("outcome", "")).split())
    if not outcome or len(outcome) > 500:
        raise write_quest.Rejected("the act review needs an outcome of at most 500 characters")
    remaining = validate_acts(answer.get("remaining_acts"), who["level"], 1, 4)
    first = index + 2
    review = {"campaign": campaign["id"], "act": index, "outcome": outcome, "remaining_acts": remaining,
              "reveals": validate_reveals(answer.get("reveals") or [], first, first + len(remaining) - 1, 0, 3),
              "dm_note": " ".join(str(answer.get("dm_note", "")).split()), "canon_add": canon_facts(answer)}
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, dm_note, model, tokens_in, tokens_out) "
               "VALUES (?, ?, ?, 'act_review', ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(review), review["dm_note"], usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: act {index + 1} is over; review proposal {number} written")
    return number


def apply_act_review(campaign, review):
    """The campaign's acts and reveals after a review: finished acts kept with the outcome, the rest replaced."""
    acts, index = loads(campaign["acts"]), review["act"]
    acts[index] = dict(acts[index], outcome=review["outcome"])
    kept = [r for r in loads(campaign["reveals"]) if r["act"] <= index + 1]
    return acts[:index + 1] + review["remaining_acts"], kept + review["reveals"]


def clip_words(text, limit):
    """At most `limit` words of text, marked with an ellipsis when cut."""
    words = str(text or "").split()
    return " ".join(words[:limit]) + (" ..." if len(words) > limit else "")


def settle_ticks():
    return int(setting("DM_SETTLE_TICKS", 3))


def dossier_for(db, zone, side, refresh=False):
    """A zone's dossier, from state.db once it has been built: stock quests do not change while the DM runs."""
    found = None if refresh else dm_state.cached_dossier(db, zone, side)
    if found is None:
        found = world_query.zone_dossier(zone, side)
        dm_state.store_dossier(db, found)
    return found


def settled_dossier(db, who):
    """The dossier of the zone this character has settled in, or None.

    None while they are on the move, in a capital, or in a zone with too few
    stock quests to ground a chapter. Each of those is an interlude: the
    chapter in force stays in force.
    """
    known = dm_state.get_character(db, who["guid"])
    if not known or known["zone_ticks"] < settle_ticks() or who["zone"] in world_query.CAPITALS:
        return None
    dossier = dossier_for(db, who["zone"], world_query.side(who["race"]))
    return dossier if dossier["quests"] >= world_query.DOSSIER_MIN_QUESTS else None


def follow_zone(db, who, say):
    """Keep the chapter in force in step with where the character has settled.

    Settling in a new zone pauses the chapter in force and ends its arc with
    `left`; coming back to a paused chapter's zone resumes it. Returns the
    zone's dossier when a new chapter is needed there, else None.
    """
    dossier = settled_dossier(db, who)
    if dossier is None:
        return None
    guid, zone = who["guid"], who["zone"]
    chapter = dm_state.active_chapter(db, guid)
    if chapter and chapter["zone"] == zone:
        return None
    if chapter:
        dm_state.set_chapter_status(db, chapter, "paused")
        say(f"  {who['name']}: settled in {world_query.zone_name(zone)}; "
            f"the chapter in {world_query.zone_name(chapter['zone'])} is paused")
        arc = dm_state.active_arc(db, guid)
        if arc:
            dm_state.end_arc(db, arc, "left")
            say(f"  {who['name']}: {arc_called(arc)} ends here")
        if dm_state.withdraw_proposals(db, guid, "arc"):
            say(f"  {who['name']}: the arc waiting for approval no longer fits and was withdrawn")
    waiting = dm_state.pending_proposal(db, guid, "chapter")
    if waiting and json.loads(waiting["payload"]).get("zone") != zone:
        dm_state.withdraw_proposals(db, guid, "chapter")
        say(f"  {who['name']}: the chapter waiting for approval is for a zone they have left; withdrawn")
    back = dm_state.paused_chapter(db, guid, zone)
    if back:
        dm_state.set_chapter_status(db, back, "active")
        say(f"  {who['name']}: back in {world_query.zone_name(zone)}; the chapter there resumes")
        return None
    return dossier


def seeds_left(chapter):
    return max(0, len(json.loads(chapter["seeds"])) - chapter["current_seed"])


def allowed_adversaries(chapter, dossier):
    """Creature ids an arc in this chapter may turn against: the zone's enemies and the chapter's own."""
    found = {enemy["creature"] for enemy in dossier["enemies"]}
    found.update(c for c in (chapter["adversary_creature"], chapter["finale_creature"]) if c is not None)
    return found


def bounty_creatures(spec):
    """Every creature a bounty's spec sends the character against, kills and trophies alike."""
    return {int(entry["creature"]) for key in ("kill", "props") for entry in spec.get(key) or []
            if entry.get("creature")}


def finish_chapter(db, chapter, reason, say, name):
    """Close a chapter, and the arc playing it out. reason: finale (turned in) or seeds (they ran out)."""
    dm_state.set_chapter_status(db, chapter, "finished", reason)
    arc = dm_state.active_arc(db, chapter["guid"])
    if arc and arc["chapter"] == chapter["id"]:
        dm_state.end_arc(db, arc, "resolved")
    how = "its finale was turned in" if reason == "finale" else "its seeds have run out"
    say(f"  {name}: the chapter in {world_query.zone_name(chapter['zone'])} is finished ({how})")


def chapter_text(chapter, heading):
    """A chapter as plain text, with each seed marked used or to come."""
    seeds = json.loads(chapter["seeds"]) if isinstance(chapter["seeds"], str) else chapter["seeds"]
    lines = [heading, f"Zone: {world_query.zone_name(chapter['zone'])}", f"Premise: {chapter['premise']}",
             f"Adversary: {chapter['adversary']}", f"Finale: {chapter['finale']}", "Seeds:"]
    for index, seed in enumerate(seeds):
        lines.append(f"{index + 1}. [{'used' if index < chapter['current_seed'] else 'to come'}] {seed}")
    lines.append(f"Hooks: {chapter['hooks'] or 'none'}")
    return "\n".join(lines)


def chapter_block(db, guid):
    """The chapter in force, as briefly as a bounty prompt can carry it, or None."""
    chapter = dm_state.active_chapter(db, guid)
    if not chapter:
        return None
    text = f"{chapter['premise']} Finale: {chapter['finale']}"
    arc = dm_state.active_arc(db, guid)
    if arc and arc["chapter"] == chapter["id"] and arc["chapter_seed"] is not None:
        text += f" Now: {json.loads(chapter['seeds'])[arc['chapter_seed']]}"
    return ("Your private chapter for this character in " + world_query.zone_name(chapter["zone"])
            + " (never state it to the player): " + clip_words(text, CHAPTER_WORDS))


def chapter_message(db, who, dossier):
    zone = world_query.zone_name(dossier["zone"])
    known = dm_state.get_character(db, who["guid"])
    lines = [f"Character: {world_query.describe(who)}, settled in {zone}.", "",
             "The zone's own story, from its stock quests:", world_query.dossier_text(dossier), ""]
    campaign = dm_state.active_campaign(db, who["guid"])
    if campaign:
        lines += ["Direction, from the campaign (follow it):", current_act_text(campaign), ""]
    earlier = dm_state.chapters_of(db, who["guid"])
    if earlier:
        lines.append("Earlier chapters for this character, oldest first:")
        for chapter in earlier:
            how = {"paused": "left unfinished", "finished": "finished", "active": "in force"}.get(chapter["status"])
            lines.append(f"- {world_query.zone_name(chapter['zone'])}, {how}: {chapter['premise']}")
        lines.append("")
    before = [c for c in earlier if c["zone"] == dossier["zone"] and c["status"] == "finished"]
    if before:
        last = before[-1]
        lines += [chapter_text(last, "The chapter that finished here (this one is its sequel):")]
        for arc in db.execute("SELECT * FROM arcs WHERE chapter = ? ORDER BY id", (last["id"],)).fetchall():
            lines.append(f"- An arc against {arc['adversary']}: " + (arc["outcome"] or arc["end_reason"] or arc["status"]))
        lines.append("")
    saga = saga_lines(db, who["guid"])
    if saga:
        lines += ["Earlier arcs, oldest first:"] + saga + [""]
    arc = dm_state.active_arc(db, who["guid"])
    if arc:
        lines += [arc_text(arc, "An arc is still in force and will run to its end before this chapter's first seed:"), ""]
    if known and known["story_so_far"]:
        lines += ["Story so far (your own summary):", known["story_so_far"], ""]
    events = dm_state.events_since(db, who["guid"], 0, limit=20)
    lines.append("What you know of them so far:")
    lines += [f"- {event['text'].split(' [')[0]}" for event in events] or ["- nothing yet"]
    facts = canon_lines(db, who["guid"], dossier["zone"], [c["creature"] for c in dossier["cast"]])
    if facts:
        lines += [""] + facts
    turned_down = db.execute("SELECT reason FROM proposals WHERE guid = ? AND status = 'rejected' AND reason <> '' "
                             "AND type = 'chapter' ORDER BY decided_at DESC LIMIT 2", (who["guid"],)).fetchall()
    if turned_down:
        lines += ["", "The server owner turned down your earlier chapters for this character, saying:"]
        lines += [f"- {row['reason']}" for row in turned_down]
    return "\n".join(lines)


def validate_chapter(answer, dossier):
    """Check the model's chapter against the zone's dossier and return it in stored form. Raises write_quest.Rejected."""
    chapter = {"zone": dossier["zone"], "side": dossier["side"]}
    for field, limit, needed in (("premise", 600, True), ("adversary", 120, True), ("finale", 300, True),
                                 ("hooks", 300, False)):
        text = " ".join(str(answer.get(field, "") or "").split())
        if (needed and not text) or len(text) > limit:
            raise write_quest.Rejected(f"chapter {field} is missing or over {limit} characters")
        chapter[field] = text
    cast = {person["creature"] for person in dossier["cast"]}
    enemies = {enemy["creature"] for enemy in dossier["enemies"]}
    local = answer.get("local_cast") or []
    if not isinstance(local, list) or len(local) > 4 or any(c not in cast for c in local):
        raise write_quest.Rejected("the chapter's cast must be up to four of the zone's quest givers")
    chapter["local_cast"] = list(dict.fromkeys(local))
    adversary = answer.get("adversary_creature")
    if enemies and adversary not in enemies:
        raise write_quest.Rejected(f"chapter adversary creature {adversary} is not among the zone's enemies")
    chapter["adversary_creature"] = adversary if adversary in enemies else None
    finale = answer.get("finale_creature")
    if finale is not None and finale not in enemies:
        raise write_quest.Rejected(f"finale creature {finale} is not among the zone's enemies")
    chapter["finale_creature"] = finale
    seeds = answer.get("seeds")
    if not isinstance(seeds, list) or not 2 <= len(seeds) <= 4:
        raise write_quest.Rejected("a chapter needs two to four seeds")
    chapter["seeds"] = [" ".join(str(seed).split()) for seed in seeds]
    if any(not seed or len(seed) > 300 for seed in chapter["seeds"]):
        raise write_quest.Rejected("each seed is one line of at most 300 characters")
    chapter["dm_note"] = " ".join(str(answer.get("dm_note", "")).split())
    chapter["canon_add"] = canon_facts(answer)
    return chapter


def propose_chapter(db, who, dossier, say):
    """Ask the model for a chapter in the zone this character has settled in. Returns the proposal number."""
    answer, usage = llm.ask_for_tool_call(CHAPTER_SYSTEM, chapter_message(db, who, dossier), CHAPTER_TOOL,
                                          max_tokens=4096, model=llm.plan_model())
    chapter = validate_chapter(answer, dossier)
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, dm_note, model, tokens_in, tokens_out) "
               "VALUES (?, ?, ?, 'chapter', ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(chapter), chapter["dm_note"], usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: chapter proposal {number} written for {world_query.zone_name(dossier['zone'])}"
        + plan_note("chapter", "adversary: " + chapter["adversary"]))
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


def parties_text(quest):
    """', given by X, turned in to Y; handed over: 12 Stolen Book', for the list of earlier bounties."""
    if not quest["giver"]:
        return ""
    text = f", given by {quest['giver']}"
    if quest["status"] == "completed":
        text += f", turned in to {quest['ender'] or quest['giver']}"
        if quest["handed_over"]:
            text += f"; handed over: {quest['handed_over']}"
    return text


HERALD_OUTCOME = {
    "accepted": "not finished yet",
    "offered": "not yet accepted",
    "ignored": "ignored",
    "purged": "withdrawn",
}


def herald_history(db, guid, creature, last=None):
    """The "With this character" line for one candidate herald: what they gave and what they received."""
    dealings = []
    for quest in dm_state.herald_history(db, guid, creature):
        when = dm_state.ago(quest["issued_at"])
        gave = quest["giver_id"] == creature
        received = quest["ender_id"] == creature and quest["status"] == "completed"
        if gave:
            text = f"gave \"{quest['title']}\" {when} ago"
            if received:
                text += " and received its turn-in"
            elif quest["status"] != "completed":
                text += f" ({HERALD_OUTCOME.get(quest['status'], quest['status'])})"
        elif received:
            text = f"received the turn-in of \"{quest['title']}\" {when} ago"
        else:
            continue                        # due to receive one that is not in yet: nothing has passed between them
        if received and quest["handed_over"]:
            text += f", with {quest['handed_over']} handed over"
        dealings.append(text)
    if not dealings:
        return "nothing yet."
    line = "; ".join(dealings) + "."
    line = line[0].upper() + line[1:]
    if last is not None and last["giver_id"] == creature:
        line += " Gave the last bounty."
    return line


def enrich_heralds(db, guid, context):
    """Add what state.db knows to each candidate herald: a settled voice note and their dealings with this character."""
    last = dm_state.last_quest(db, guid)
    chapter = dm_state.active_chapter(db, guid)
    cast = set(json.loads(chapter["local_cast"] or "[]")) if chapter else set()
    for herald in context["givers"]:
        if herald["creature"] in cast:
            herald["chapter_cast"] = True
        note = dm_state.voice_note(db, herald["creature"])
        if note:
            herald["voice_note"] = note["note"]
        herald["history"] = herald_history(db, guid, herald["creature"], last)
    return context


RECEIPT = re.compile(r"\b(brought me|gave me|given me|returned to me|as I asked|my last task)\b", re.IGNORECASE)


def receipt_warning(db, guid, giver_id, texts):
    """A herald with no dealings with this character who speaks of something received from them.

    A warning, not a rejection: the wording may be innocent, but it is the
    common way one herald takes credit for another's bounty.
    """
    if giver_id is None or dm_state.herald_history(db, guid, giver_id, limit=1):
        return None
    found = sorted({match.group(1).lower() for text in texts for match in RECEIPT.finditer(str(text or ""))})
    if not found:
        return None
    return ("the herald has had no dealings with this character, yet says "
            + ", ".join(f'"{phrase}"' for phrase in found) + "; check it is not another herald's bounty")


def propose(db, who, say):
    """Ask the model for the next bounty and store it as a proposal. Returns the proposal number."""
    context = write_quest.gather(who["name"], skip=recent_targets(db, who["guid"]),
                                 props_in_use=props_in_use(db), gear_tiers=gear_tiers(db, who))
    enrich_heralds(db, who["guid"], context)
    message = write_quest.user_message(context, None, story=story_section(db, context["character"], context["givers"]))
    answer, usage = llm.ask_for_tool_call(SYSTEM, message, TOOL, max_tokens=4096)
    progress = answer.get("beat_progress")
    if progress not in ("advance", "hold", "detour", "conclude"):
        progress = "hold"
    arc = dm_state.active_arc(db, who["guid"])
    finale = progress == "conclude" and arc and arc["current_beat"] >= len(json.loads(arc["beats"])) - 1
    if progress == "conclude" and not finale:
        progress = "hold"                           # a finale before the last beat is just another bounty
    # An arc's finale is significant whatever its kind, so it may pay a prize.
    spec, target, announcement = write_quest.build_spec(answer, context, hot_quest.QUEST_ID_RANGE[0],
                                                        finale=bool(finale))
    beat = " ".join(str(answer.get("story_beat", "")).split())
    summary = " ".join(str(answer.get("story_so_far", "")).split())
    if not beat or not summary:
        raise write_quest.Rejected("the model left out the story beat or the story summary")
    if len(summary.split()) > 160:
        raise write_quest.Rejected("the story summary is far over 120 words")
    payload = {"beat_progress": progress, "kind": target["kind"], "needs_party": target["needs_party"],
               "canon_add": canon_facts(answer)}
    if finale:
        payload["concludes_arc"] = arc["id"]
    db.execute("INSERT INTO proposals (ts, guid, name, type, payload, spec, target, announcement, dm_note, story_beat, "
               "story_so_far, model, tokens_in, tokens_out) VALUES (?, ?, ?, 'bounty', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (dm_state.now(), who["guid"], who["name"], json.dumps(payload), json.dumps(spec),
                json.dumps(target), announcement, answer.get("dm_note", ""), beat, summary, usage.get("model"),
                usage.get("input_tokens"), usage.get("output_tokens")))
    number = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    say(f"  {who['name']}: bounty proposal {number} written, \"{spec['title']}\"")
    warning = receipt_warning(db, who["guid"], spec["giver"],
                              (spec["briefing"], spec["progress_text"], spec["completion_text"]))
    if warning:
        say(f"    warning: {warning}")
    return number


def approve_proposal(db, row, say):
    """Carry out one pending proposal. Raises ApproveFailed if nothing was done."""
    stamp = dm_state.now()
    if row["type"] == "campaign":
        campaign = json.loads(row["payload"])
        db.execute("UPDATE campaigns SET status = 'replaced', updated_at = ? WHERE guid = ? AND status = 'active'",
                   (stamp, row["guid"]))
        db.execute("INSERT INTO campaigns (guid, premise, question, stake, acts, cast, reveals, seed, model, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (row["guid"], campaign["premise"], campaign["question"], campaign["stake"],
                    json.dumps(campaign["acts"]), json.dumps(campaign["cast"]), json.dumps(campaign["reveals"]),
                    campaign.get("seed", ""), row["model"], stamp, stamp))
        campaign_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        establish(db, row["guid"], None, None, campaign.get("canon_add"), f"campaign {campaign_id}")
        db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
        db.commit()
        say(f"campaign for {row['name']} is now in force.")
        return
    if row["type"] == "act_review":
        review = json.loads(row["payload"])
        campaign = dm_state.active_campaign(db, row["guid"])
        if not campaign or campaign["id"] != review["campaign"] or campaign["current_act"] != review["act"]:
            raise ApproveFailed("the campaign has changed since this review was written; reject it and the "
                                "next tick will write another")
        acts, reveals = apply_act_review(campaign, review)
        db.execute("UPDATE campaigns SET acts = ?, reveals = ?, current_act = ?, updated_at = ? WHERE id = ?",
                   (json.dumps(acts), json.dumps(reveals), review["act"] + 1, stamp, campaign["id"]))
        establish(db, row["guid"], None, None, review.get("canon_add"), f"campaign {campaign['id']}")
        db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
        db.commit()
        say(f"{row['name']}'s campaign has moved on to act {review['act'] + 2}.")
        return
    if row["type"] == "chapter":
        chapter = json.loads(row["payload"])
        in_force = dm_state.active_chapter(db, row["guid"])
        if in_force:
            dm_state.set_chapter_status(db, in_force, "paused")
        campaign = dm_state.active_campaign(db, row["guid"])
        db.execute("INSERT INTO chapters (guid, zone, premise, local_cast, adversary, adversary_creature, seeds, "
                   "finale, finale_creature, hooks, campaign_act, model, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (row["guid"], chapter["zone"], chapter["premise"], json.dumps(chapter["local_cast"]),
                    chapter["adversary"], chapter["adversary_creature"], json.dumps(chapter["seeds"]),
                    chapter["finale"], chapter["finale_creature"], chapter["hooks"],
                    campaign["current_act"] if campaign else None, row["model"], stamp, stamp))
        chapter_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        establish(db, row["guid"], chapter["zone"], None, chapter.get("canon_add"), f"chapter {chapter_id}")
        db.execute("UPDATE proposals SET status = 'approved', decided_at = ? WHERE id = ?", (stamp, row["id"]))
        db.commit()
        say(f"chapter for {row['name']} in {world_query.zone_name(chapter['zone'])} is now in force.")
        return
    if row["type"] == "arc":
        arc = json.loads(row["payload"])
        in_force = dm_state.active_arc(db, row["guid"])
        if in_force:                                    # a reseed replaces the arc in force
            dm_state.end_arc(db, in_force, "replaced")
        ended = dm_state.past_arcs(db, row["guid"])
        if ended and not ended[-1]["outcome"] and arc.get("previous_outcome"):
            db.execute("UPDATE arcs SET outcome = ? WHERE id = ?", (arc["previous_outcome"], ended[-1]["id"]))
        db.execute("INSERT INTO arcs (guid, premise, lure, adversary, adversary_creature, beats, signature_reward, "
                   "seed, chapter, chapter_seed, model, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (row["guid"], arc["premise"], arc["lure"], arc["adversary"], arc.get("adversary_creature"),
                    json.dumps(arc["beats"]), arc["signature_reward"], arc.get("seed", ""), arc.get("chapter"),
                    arc.get("chapter_seed"), row["model"], stamp, stamp))
        arc_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if arc.get("chapter_seed") is not None:            # the seed is spent; the next arc takes the one after
            db.execute("UPDATE chapters SET current_seed = MAX(current_seed, ?), updated_at = ? WHERE id = ?",
                       (arc["chapter_seed"] + 1, stamp, arc["chapter"]))
        establish(db, row["guid"], arc.get("zone"), None, arc.get("canon_add"), f"arc {arc_id}")
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
    herald = target.get("giver") or {}
    ender = herald.get("name") if spec["ender"] == spec["giver"] else None
    db.execute("INSERT INTO quests (quest, guid, title, kind, objectives, target, giver, giver_id, ender, ender_id, "
               "handed_over, concludes_arc, spec, announcement, dm_note, story_beat, model, issued_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (spec["id"], row["guid"], spec["title"], payload.get("kind"),
                json.dumps(target.get("objectives") or []), target["name"],
                herald.get("name"), spec["giver"], ender, spec["ender"],
                dm_state.handed_over(target.get("objectives") or []),
                payload.get("concludes_arc"), json.dumps(spec), row["announcement"], row["dm_note"],
                row["story_beat"], row["model"], stamp))
    if dm_state.settle_voice(db, spec["giver"], herald.get("name"), target.get("herald_voice"), spec["id"]) == "settled":
        say(f"{herald.get('name')}'s voice is now settled: {target['herald_voice']}")
    record_reward(db, row, spec, target)
    establish(db, row["guid"], spec.get("zone"), spec["giver"], payload.get("canon_add"), f"bounty {spec['id']}")
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


def record_reward(db, row, spec, target):
    """Write the bounty's headline reward to the ledger, which the gear budget reads."""
    gear = target.get("gear")
    known = dm_state.get_character(db, row["guid"])
    level = known["level"] if known else 0
    if gear:
        dm_state.record_reward(db, row["guid"], spec["id"], gear["tier"], [g["item"] for g in gear["items"]],
                               [g["name"] for g in gear["items"]], level)
    elif target.get("capstone"):
        book = target["capstone"]
        dm_state.record_reward(db, row["guid"], spec["id"], "capstone", [book["item"]], [book["name"]], level)


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
                say(f"  {who['name']}: outgrew {arc_called(arc)}; a new one will be planned")
            # The campaign is written first, and reviewed whenever the character levels out of an act.
            # Neither holds up an arc or a bounty already under way; a chapter waits for them.
            campaign = dm_state.active_campaign(db, who["guid"])
            planning = None
            if not campaign:
                planning = "campaign"
                if not dm_state.pending_proposal(db, who["guid"], "campaign"):
                    if dm_state.proposals_in_last_hour(db) >= ceiling:
                        say("  hourly ceiling on model calls reached; no more proposals this tick")
                        break
                    if settle(db, propose_campaign(db, who, None, say), say):
                        planning = None
                    db.commit()
            elif act_left(campaign, who["level"]):
                planning = "act_review"
                if not dm_state.pending_proposal(db, who["guid"], "act_review"):
                    if dm_state.proposals_in_last_hour(db) >= ceiling:
                        say("  hourly ceiling on model calls reached; no more proposals this tick")
                        break
                    if settle(db, propose_act_review(db, who, campaign, say), say):
                        planning = None
                    db.commit()
            # A chapter follows the character to wherever they settle.
            dossier = follow_zone(db, who, say)
            if dossier:
                if planning:
                    what = "campaign" if planning == "campaign" else "act review"
                    say(f"  {who['name']}: the chapter for {world_query.zone_name(who['zone'])} waits for the {what}")
                elif dm_state.pending_proposal(db, who["guid"], "chapter"):
                    say(f"  {who['name']}: a chapter for {world_query.zone_name(who['zone'])} is waiting for approval")
                elif dm_state.proposals_in_last_hour(db) >= ceiling:
                    say("  hourly ceiling on model calls reached; no more proposals this tick")
                    break
                else:
                    settle(db, propose_chapter(db, who, dossier, say), say)
                    db.commit()
            # An arc comes next, from the chapter's next seed: no bounty is written for a character
            # without an approved plan. An arc already in force runs on wherever they go.
            if not dm_state.active_arc(db, who["guid"]):
                if dm_state.pending_proposal(db, who["guid"], "arc"):
                    say(f"  {who['name']}: no proposal (an arc is waiting for approval)")
                    continue
                chapter = dm_state.active_chapter(db, who["guid"])
                if not chapter:
                    waiting = "a chapter is waiting for approval" if dm_state.pending_proposal(
                        db, who["guid"], "chapter") else "not yet settled where a chapter can be written"
                    say(f"  {who['name']}: no proposal ({waiting})")
                    continue
                if not seeds_left(chapter):
                    finish_chapter(db, chapter, "seeds", say, who["name"])
                    db.commit()
                    continue
                if dm_state.proposals_in_last_hour(db) >= ceiling:
                    say("  hourly ceiling on model calls reached; no more proposals this tick")
                    break
                if not settle(db, propose_arc(db, who, None, say, chapter), say):
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

def voice_text(db, creature, written):
    """How the proposal's voice note stands against the one on record."""
    stored = dm_state.voice_note(db, creature) if creature is not None else None
    written = " ".join(str(written or "").split())
    if not stored:
        return f"new, kept on approval: {written}" if written else "none given"
    pinned = ", pinned" if stored["pinned"] else ""
    if written == stored["note"]:
        return f"settled{pinned}: {stored['note']}"
    return f"settled{pinned}, and kept: {stored['note']}\n          (the model wrote: {written or 'nothing'})"


def show_proposal(db, row, reveal=False):
    if row["type"] in sealed() and not reveal:
        print(f"\n=== Proposal {row['id']}: {row['type'].upper()} for {row['name']}  "
              f"({dm_state.ago(row['ts'])} ago, {row['status']}): sealed; --reveal to read it ===")
        return
    if row["type"] == "campaign":
        campaign = dict(json.loads(row["payload"]), current_act=0)
        print(f"\n=== Proposal {row['id']}: CAMPAIGN for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===")
        print(campaign_text(campaign, "A private plan for this character's whole life. The largest spoiler there is."))
        if campaign.get("seed"):
            print(f"Your direction: {campaign['seed']}")
        for fact in campaign.get("canon_add") or []:
            print(f"Establishes: {fact}")
        print(f"Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]")
        return
    if row["type"] == "act_review":
        review = json.loads(row["payload"])
        print(f"\n=== Proposal {row['id']}: ACT REVIEW for {row['name']}  ({dm_state.ago(row['ts'])} ago, "
              f"{row['status']}) ===")
        print(f"Act {review['act'] + 1} ended: {review['outcome']}\nWhat remains:")
        for number, act in enumerate(review["remaining_acts"], review["act"] + 2):
            print(f"{number}. levels {act['level_band']}: {act['intent']} (thread: {act['thread']})")
        for reveal in review["reveals"]:
            print(f"Reveal, act {reveal['act']}: {reveal['text']}")
        for fact in review.get("canon_add") or []:
            print(f"Establishes: {fact}")
        print(f"Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]")
        return
    if row["type"] == "chapter":
        chapter = dict(json.loads(row["payload"]), current_seed=0)
        print(f"\n=== Proposal {row['id']}: CHAPTER for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===")
        print(chapter_text(chapter, "A private plan. Reading it is a spoiler if you play this character."))
        print(f"Cast: {', '.join(str(c) for c in chapter['local_cast']) or 'none'}   "
              f"adversary creature: {chapter['adversary_creature']}   finale creature: {chapter['finale_creature']}")
        for fact in chapter.get("canon_add") or []:
            print(f"Establishes: {fact}")
        print(f"Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]")
        return
    if row["type"] == "arc":
        arc = json.loads(row["payload"])
        print(f"\n=== Proposal {row['id']}: ARC for {row['name']}  ({dm_state.ago(row['ts'])} ago, {row['status']}) ===")
        print(arc_text(arc, "A private plan. Reading it is a spoiler if you play this character."))
        if arc.get("seed"):
            print(f"Your direction: {arc['seed']}")
        for fact in arc.get("canon_add") or []:
            print(f"Establishes: {fact}")
        print(f"Model's note: {row['dm_note']}   [{row['model']}, tokens {row['tokens_in']}/{row['tokens_out']}]")
        return
    spec, target = json.loads(row["spec"]), json.loads(row["target"])
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
Voice:    {voice_text(db, spec.get('giver'), target.get('herald_voice'))}
Hunt:     {objectives}
Reward:   {write_quest.reward_text(spec, target)}
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
    for fact in payload.get("canon_add") or []:
        print(f"Establishes:  {fact}")
    warning = receipt_warning(db, row["guid"], spec.get("giver"),
                              (spec["briefing"], spec["progress_text"], spec["completion_text"]))
    if warning:
        print(f"** warning: {warning}")


def cmd_pending(db, args):
    stopped = console.paused()
    if stopped:
        print(f"** the DM is paused ({stopped}). Nothing can reach the game until you resume. **\n")
    found = db.execute("SELECT * FROM proposals WHERE status = 'pending' ORDER BY id").fetchall()
    if not found:
        print("no proposals waiting.")
    for row in found:
        show_proposal(db, row, reveal=args.reveal)
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
            settle(db, propose_arc(db, who, args.seed, print, dm_state.active_chapter(db, who["guid"])), print)
        except (write_quest.Rejected, llm.LLMError) as error:
            sys.exit(f"dm: no arc written: {error}")
        db.commit()
        return
    if "arc" in sealed() and not args.reveal:
        arc = dm_state.active_arc(db, who["guid"])
        print(f"{who['name']}: {len(dm_state.past_arcs(db, who['guid'], limit=1000))} arc(s) ended, "
              f"{'one' if arc else 'none'} in force. Arcs are sealed; --reveal to read them.")
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


def cmd_campaign(db, args):
    """Show a character's campaign, or have a new one written from a direction of yours."""
    if args.seed:
        who = world_query.character(args.character)
        if not who:
            sys.exit(f"dm: no character named {args.character}")
        if not dm_state.get_character(db, who["guid"]):
            sys.exit("dm: the Overseer has not noticed this character yet; run a tick while they are online")
        waiting = dm_state.pending_proposal(db, who["guid"], "campaign")
        if waiting:
            sys.exit(f"dm: campaign proposal {waiting['id']} is already waiting; approve or reject it first")
        try:
            settle(db, propose_campaign(db, who, args.seed, print), print)
        except (write_quest.Rejected, llm.LLMError) as error:
            sys.exit(f"dm: no campaign written: {error}")
        db.commit()
        return
    row, _held = whose_story(db, args.character)
    if not row:
        sys.exit(f"dm: the Overseer has not noticed anyone called {args.character}")
    campaign = dm_state.active_campaign(db, row["guid"])
    if not campaign:
        print(f"{row['name']} has no campaign yet. One is written on the next tick, or give a direction with --seed.")
        return
    acts = loads(campaign["acts"])
    if "campaign" in sealed() and not args.reveal:
        print(f"{row['name']}: a campaign of {len(acts)} acts, now in act {campaign['current_act'] + 1}. "
              "Campaigns are sealed; --reveal to read it.")
        return
    print(f"=== Campaign for {row['name']} (the largest spoiler there is if you play this character) ===\n")
    print(campaign_text(campaign, f"Written {dm_state.ago(campaign['created_at'])} ago:"))
    if campaign["seed"]:
        print(f"Your direction: {campaign['seed']}")


def cmd_chapter(db, args):
    """Show a character's zone chapters. A spoiler."""
    row, _held = whose_story(db, args.character)
    if not row:
        sys.exit(f"dm: the Overseer has not noticed anyone called {args.character}")
    found = dm_state.chapters_of(db, row["guid"])
    if "chapter" in sealed() and not args.reveal:
        print(f"{row['name']}: {len(found)} chapter(s)" + "".join(
            f"\n  {world_query.zone_name(c['zone'])}: {c['status']}" for c in found)
            + "\nChapters are sealed; --reveal to read them.")
        return
    print(f"=== Chapters for {row['name']} (spoilers if you play this character) ===")
    if not found:
        print(f"\nNone yet. One is written once they spend {settle_ticks()} ticks in a zone with its own quests.")
    for chapter in found:
        how = chapter["status"] + (f", {chapter['end_reason']}" if chapter["end_reason"] else "")
        print("\n" + chapter_text(chapter, f"[{how}] since {dm_state.ago(chapter['created_at'])} ago:"))


def cmd_dossier(db, args):
    """Print a zone's dossier as the model is shown it."""
    side = args.side or "alliance"
    dossier = dossier_for(db, args.zone, side, refresh=args.refresh)
    db.commit()
    text = world_query.dossier_text(dossier)
    print(f"zone {args.zone}, {side} ({len(text)} characters)\n{text}")
    if dossier["quests"] < world_query.DOSSIER_MIN_QUESTS:
        print(f"(fewer than {world_query.DOSSIER_MIN_QUESTS} quests: no chapter is written here)")


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
        dm_state.set_reward_status(db, quest["id"], "lapsed")
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
                                     gear_tiers=gear_tiers(db, who),
                                     props_in_use=props_in_use(db))
    except write_quest.NoContext as error:
        sys.exit(f"dm: {error}")
    enrich_heralds(db, who["guid"], context)
    hidden = set() if args.reveal else sealed()
    print(write_quest.user_message(context, None,
                                   story=story_section(db, context["character"], context["givers"], hidden)))
    if hidden & {"arc", "chapter", "campaign"}:
        print("\n(sealed plans are left out above; the model is shown them. --reveal to read them.)")


def cmd_voices(db, _args):
    found = dm_state.all_voices(db)
    if not found:
        print("no herald has a settled voice yet.")
    for row in found:
        pinned = ", pinned" if row["pinned"] else ""
        print(f"{row['name']} ({row['creature']}), {row['uses']} bounties{pinned}:\n  {row['note']}")


def find_herald(db, wanted):
    """(creature id, name) for an id or a name, looking at notes already settled before the world database."""
    wanted = " ".join(str(wanted).split())
    if wanted.isdigit():
        stored = dm_state.voice_note(db, int(wanted))
        if stored:
            return stored["creature"], stored["name"]
        found = world_query.herald_voice(int(wanted))
        if not found:
            sys.exit(f"dm: no creature {wanted}")
        return found["creature"], found["name"]
    stored = db.execute("SELECT creature, name FROM herald_voices WHERE name = ? COLLATE NOCASE", (wanted,)).fetchall()
    if len(stored) == 1:
        return stored[0]["creature"], stored[0]["name"]
    found = world_query.heralds_named(wanted)
    exact = [row for row in found if row["name"].lower() == wanted.lower()]
    if len(exact) == 1 or len(found) == 1:
        row = (exact or found)[0]
        return row["creature"], row["name"]
    if not found:
        sys.exit(f"dm: no quest-giver with one spawn is called anything like {wanted!r}")
    listed = "\n".join(f"  {row['creature']}: {row['name']}" + (f", {row['title']}" if row["title"] else "")
                       for row in found)
    sys.exit(f"dm: {wanted!r} could be any of these; use the id:\n{listed}")


def cmd_voice(db, args):
    creature, name = find_herald(db, args.npc)
    if args.forget:
        if dm_state.forget_voice(db, creature):
            db.commit()
            print(f"{name}'s voice note is cleared; the next bounty through them settles a new one.")
        else:
            print(f"{name} has no voice note.")
        return
    if args.note is not None:
        if not args.note.strip():
            sys.exit("dm: the note is empty; to clear one, use --forget")
        dm_state.set_voice(db, creature, name, args.note)
        db.commit()
        print(f"{name}'s voice note is set and pinned.")
        return
    found = world_query.herald_voice(creature) or {"facts": "", "lines": []}
    print(f"{name} ({creature})" + (f": {found['facts']}" if found["facts"] else ""))
    if found["lines"]:
        for line in found["lines"]:
            print(f"  [{line['src']}] \"{line['text']}\"")
    else:
        print("  (no lines of their own in the database)")
    stored = dm_state.voice_note(db, creature)
    if stored:
        how = "pinned by you" if stored["pinned"] else (f"settled by bounty {stored['set_by_quest']}"
                                                        if stored["set_by_quest"] else "settled")
        print(f"Voice ({how}, {stored['uses']} bounties): {stored['note']}")
    else:
        print("Voice: not settled yet.")


def cmd_canon(db, args):
    """List the facts the story has established, add one of yours, or withdraw one."""
    if args.retire is not None:
        if not dm_state.retire_canon(db, args.retire):
            sys.exit(f"dm: no active fact {args.retire}")
        db.commit()
        print(f"fact {args.retire} is withdrawn; no prompt will be shown it again.")
        return
    guid = None
    if args.character:
        row, _held = whose_story(db, args.character)
        if not row:
            sys.exit(f"dm: the Overseer has not noticed anyone called {args.character}")
        guid = row["guid"]
    if args.add is not None:
        fact = dm_state.clean_fact(args.add)
        if not fact:
            sys.exit(f"dm: a fact is one sentence of at most {dm_state.CANON_WORDS} words")
        number = dm_state.add_canon(db, guid, args.zone, args.creature, fact, "owner")
        if number is None:
            sys.exit("dm: that fact is already established")
        db.commit()
        about = [args.character] if guid else []
        if args.zone is not None:
            about.append(world_query.zone_name(args.zone))
        if args.creature is not None:
            about.append(f"creature {args.creature}")
        print(f"fact {number} established, about {', '.join(about)}." if about
              else f"fact {number} established; every prompt is shown it.")
        return
    found = dm_state.all_canon(db, guid)
    shown = sealed() if args.reveal else set()
    hidden = [row for row in found if fact_sealed(row, shown)]
    found = [row for row in found if not fact_sealed(row, shown)]
    if not found and not hidden:
        print("no facts established yet.")
    if hidden:
        print(f"({len(hidden)} fact(s) from sealed plans not shown; --reveal to read them)")
    for row in found:
        about = []
        if row["guid"] is not None:
            known = dm_state.get_character(db, row["guid"])
            about.append(known["name"] if known else f"guid {row['guid']}")
        if row["zone"] is not None:
            about.append(world_query.zone_name(row["zone"]))
        if row["creature"] is not None:
            about.append(f"creature {row['creature']}")
        print(f"{row['id']:>4}  {row['fact']}\n      [{', '.join(about) or 'server-wide'}; {row['source']}, "
              f"{dm_state.ago(row['ts'])} ago]")


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
    pending = commands.add_parser("pending", help="list proposals waiting for approval")
    pending.add_argument("--reveal", action="store_true", help="print sealed proposals too")
    pending.set_defaults(run=cmd_pending)
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
    arc.add_argument("--reveal", action="store_true", help="read it even if arcs are sealed")
    arc.set_defaults(run=cmd_arc)
    campaign = commands.add_parser("campaign", help="show a character's campaign, or have one written (a spoiler)")
    campaign.add_argument("character")
    campaign.add_argument("--seed", metavar="TEXT", help="a direction; a new campaign is written around it")
    campaign.add_argument("--reveal", action="store_true", help="read it even if campaigns are sealed")
    campaign.set_defaults(run=cmd_campaign)
    chapter = commands.add_parser("chapter", help="show a character's zone chapters (a spoiler)")
    chapter.add_argument("character")
    chapter.add_argument("--reveal", action="store_true", help="read them even if chapters are sealed")
    chapter.set_defaults(run=cmd_chapter)
    dossier = commands.add_parser("dossier", help="a zone's own story, from its stock quests")
    dossier.add_argument("zone", type=int)
    dossier.add_argument("--side", choices=("alliance", "horde"), help="whose quests (default alliance)")
    dossier.add_argument("--refresh", action="store_true", help="rebuild it from the world database")
    dossier.set_defaults(run=cmd_dossier)
    pause = commands.add_parser("pause", help="stop everything reaching the game")
    pause.add_argument("reason", nargs="?", default="", help="noted in the chronicle and shown on every refusal")
    pause.set_defaults(run=cmd_pause)
    commands.add_parser("resume", help="undo pause").set_defaults(run=cmd_resume)
    purge = commands.add_parser("purge", help="remove every DM quest from the game")
    purge.add_argument("--yes", action="store_true", help="skip the confirmation")
    purge.set_defaults(run=cmd_purge)
    context = commands.add_parser("context", help="show what the model would be told; no model call")
    context.add_argument("character")
    context.add_argument("--reveal", action="store_true", help="include sealed plans")
    context.set_defaults(run=cmd_context)
    commands.add_parser("voices", help="every herald's settled voice note").set_defaults(run=cmd_voices)
    voice = commands.add_parser("voice", help="show one herald's voice, or set or clear its note")
    voice.add_argument("npc", help="creature id or name")
    voice.add_argument("note", nargs="?", help="a new note, which is pinned")
    voice.add_argument("--forget", action="store_true", help="clear the note")
    voice.set_defaults(run=cmd_voice)
    canon = commands.add_parser("canon", help="list, add or withdraw established facts")
    canon.add_argument("character", nargs="?", help="only this character's facts, and the server-wide ones")
    canon.add_argument("--add", metavar="TEXT", help="state a fact yourself; for the character, if one is named")
    canon.add_argument("--zone", type=int, help="with --add: the zone id it concerns")
    canon.add_argument("--creature", type=int, help="with --add: the creature id it concerns")
    canon.add_argument("--retire", type=int, metavar="ID", help="withdraw a fact")
    canon.add_argument("--reveal", action="store_true", help="include facts from sealed plans")
    canon.set_defaults(run=cmd_canon)
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
