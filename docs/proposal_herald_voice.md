# Proposal: herald voice and continuity

Status: proposed 2026-10-06. Polish; no new game mechanics.
Audience: whoever implements it (currently Claude Code).
Reference: `herald_voice_prototype.py` beside this file is the query in 4.1, as run against the test database.

## 1. Problem

Two faults in the quest text, both seen in play:

1. **An NPC has no fixed voice.** The same person sounds like a crook in one bounty and an orator in the next.
2. **Heralds take credit for each other's dealings.** A bounty turned in to NPC A is later mentioned by NPC B as "the book you brought me".

Both come from what the model is given:

- The herald list shows only a name, a title, a distance and a direction (`write_quest.user_message`). The model invents the personality each time, with nothing to anchor it.
- Earlier bounties are listed as "offered through <name>" (`dm.story_section`), in one flat list. Nothing says which of the nearby NPCs was that person, or that the current herald was not.
- The only rule on voice is "write their lines as that person would speak".

## 2. Decisions

**A. Take each NPC's voice from their own lines, which the database already holds.** No scraping.

**B. Settle a voice note per NPC and reuse it.** The first time an NPC is used, the model records how it wrote them. Every later bounty through that NPC is shown the note.

**C. Tell the model who was party to what.** Each candidate herald is listed with their own history with this character, and a rule says a herald may only speak of what they were part of.

**D. Prefer the herald the character already knows.**

### Why not scrape Wowhead

- The NPC's own stock dialogue is a better source than a description of them, and it is already local.
- Wowhead's NPC pages are data and player comments, not personality write-ups. The wikis have biographies only for notable characters, and a herald is usually a minor one.
- It would add a network dependency, a parser to maintain, and someone else's terms of use.

It can be revisited for the NPCs with no lines at all (section 5).

## 3. Facts this rests on

Checked against the live-schema world database.

| Fact | Number |
|---|---|
| NPCs that can be heralds (quest-giver flag, one spawn) | 2,362 |
| Have stock quest text of their own, offering or at turn-in | 1,536 |
| Have a quest-giver greeting | 208 |
| Have small-talk text | 1,132 |
| **Have at least one line of their own** | **1,907 (81%)** |
| Have none: title, role, sex and level only | 455 |

Where the lines are:

| Kind | Table and column |
|---|---|
| Greeting | `questgiver_greeting.Text` where `Type = 0` |
| Offering a quest | `quest_template.Details`, through `creature_questrelation` |
| At a turn-in | `quest_template.OfferRewardText`, through `creature_involvedrelation` |
| Small talk | `creature_template.GossipMenuId` to `gossip_menu.text_id` to `npc_text_broadcast_text.BroadcastTextId0` to `broadcast_text.Text` (or `Text1`) |
| Sex | `creature_model_info.gender` by `creature_template.DisplayId1` (0 male, 1 female) |
| Role | `creature_template.NpcFlags`: 4 vendor, 8 flight master, 16 trainer, 128 innkeeper, 256 banker |

What the prototype returns for two Northshire NPCs who currently get an invented voice:

> **Marshal McBride** (male, level 20)
> "$N, my scouts tell me that the kobold infestation is larger than we had thought. A group of kobold workers has camped near the Echo Ridge Mine to the north. Go to the mine and remove them."
>
> **Deputy Willem** (male, level 18)
> "Hello there, $c. Normally I'd be out on the beat looking after the folk of Stormwind, but a lot of the Stormwind guards are fighting in the other lands. So here I am, deputized and offering bounties when I'd rather be on patrol..."

One is clipped and military, the other chatty and put-upon. That difference is what the model lacks today.

## 4. Design

### 4.1 `world_query.herald_voice(entry)`

Returns facts (title, role, sex, level) and up to three of the NPC's own lines.

- Exclude DM quests: `quest < 30000`.
- Order of preference: greeting, then quest offers, then turn-in text, then small talk. At most two quest offers and one of each other kind.
- Trim each line to about 260 characters at a sentence end. Replace `$B` with a space. Leave `$N`, `$C` and `$R` as they are.
- Drop near-duplicates. Many NPCs have several quests with the same opening (McBride's four class letters), so compare the first 50 letters.
- Skip anything under 30 characters.

### 4.2 Voice notes

A new table in `state.db`:

```sql
CREATE TABLE IF NOT EXISTS herald_voices (
    creature INTEGER PRIMARY KEY, name TEXT,
    note TEXT,              -- two sentences on how this NPC speaks
    pinned INTEGER NOT NULL DEFAULT 0,   -- 1 when the owner wrote or confirmed it
    set_at INTEGER, set_by_quest INTEGER, uses INTEGER NOT NULL DEFAULT 0
);
```

- The model's form gains `herald_voice`: "Two sentences, at most 40 words, on how the herald you chose speaks: register, sentence length, habits, attitude to adventurers. If a settled note was shown for them, repeat it unchanged."
- On approval, if the chosen herald has no note, store it. If one exists, keep the stored one and add one to `uses`.
- No extra model call: the note comes from the same call that writes the bounty.

Owner commands:

| Command | Effect |
|---|---|
| `dm.py voices` | List every settled note |
| `dm.py voice <name or id>` | Show one NPC's facts, lines and note |
| `dm.py voice <name or id> "text"` | Write or replace the note, and pin it |
| `dm.py voice <name or id> --forget` | Clear it, so the next use settles a new one |

### 4.3 The herald list in the prompt

Each candidate becomes a short block:

```
- 197: Marshal McBride, male, level 20. 32 yards east.
  Voice (settled, follow it): Clipped and official. Gives orders, not requests, and counts things.
  With this character: gave "Teeth in the Dark" 2 days ago and received its turn-in. Gave the last bounty.
- 823: Deputy Willem, male, level 18. 41 yards east.
  In his own words: "Hello there, $c. Normally I'd be out on the beat..." / "Garrick Padfoot - a cutthroat who's plagued our farmers..."
  With this character: nothing yet.
```

- Show the settled note if there is one; otherwise show the NPC's lines; otherwise only the facts.
- To bound the prompt: lines for the five nearest candidates only, two lines each when more than four candidates are listed.

### 4.4 Who was party to what

Record more per bounty. `quests` in `state.db` gains:

| Column | Holds |
|---|---|
| `giver_id` | Creature id of the NPC who offered it (the name is already stored) |
| `ender_id`, `ender` | Who received the turn-in. Same as the giver today; separate so delivery bounties can differ later |
| `handed_over` | What the character physically gave at turn-in, such as "12 Stolen Book" for a trophy. Empty for kill bounties |

Then:

- **Per candidate:** the "With this character" line in 4.3, built from the last two bounties where that NPC was giver or ender.
- **In "Earlier bounties":** replace "offered through X" with "given by X, turned in to X", and add "handed over: ..." when there was something.

### 4.5 Rules added to the prompt

Replace the single voice rule in `RULES` with:

- Write the herald's lines the way that person talks. Follow their settled voice note if one is shown; otherwise match their own lines for vocabulary, sentence length and formality. Use the same voice in the briefing, the progress text and the turn-in.
- The Overseer shows in what the herald asks for and what they know, never in how they talk. A deputy still sounds like a deputy.
- A herald knows what they were part of. They may say "you brought me" only for something on their own "With this character" line.
- To mention something another herald handled, attribute it: "I hear you carried Marshal McBride his books." Or let the herald be uneasy at knowing it without being told. Never have them claim it.
- Prefer the herald who gave the last bounty when they are on the list. Change herald when the story has moved somewhere else or the errand plainly belongs to someone else.

### 4.6 A soft check

In `dm.py pending`, print a warning, not a rejection, when the chosen herald has no history with the character and the text contains a first-person receipt: "brought me", "gave me", "returned to me", "as I asked", "my last task". It is a prompt for the owner to look, and a signal in the log once approval is automatic.

## 5. Limits

- **455 NPCs have no lines.** They get facts only, and their first use settles a note from the model's own judgment. That note then holds, so they are at least consistent. This is the group where an outside source could add something later.
- **Stock lines are in the game's voice for that NPC, not the Overseer's.** That is the point: the herald is the mask.
- **A settled note can be wrong.** `dm.py voice` exists to fix or pin it.
- **Prose is not validated.** The rules reduce the continuity fault; the soft check catches the common wording.
- **Existing bounties have no `giver_id`.** Back-fill by name where the name is unique, and leave the rest empty.

## 6. Tests to add

Offline:

- `herald_voice`: trimming at a sentence end, `$B` handling, duplicate openings dropped, the per-kind limits. Use a hand-built set of lines.
- A stored note is kept when the model returns a different one; a pinned note is never replaced.
- The "With this character" line for an NPC with history, and "nothing yet" for one without.
- The soft check fires on "the book you brought me" from a herald with no history, and not from the one who received it.

With the stub model: two bounties through the same NPC, the second prompt contains the note the first one settled.

## 7. Done when

- A second bounty through the same NPC is written in the voice the first one settled.
- `python3 dm.py context <Character>` shows each nearby NPC with their lines or note and their history with the character.
- A bounty from NPC B that follows one turned in to NPC A no longer has B claiming what A received.

## 8. Decisions for the owner

1. **What a herald knows of other heralds' dealings.** Proposed: hearsay ("I hear you...") or unease at knowing. The alternative is that heralds never mention them at all.
2. **How strongly to prefer the last herald.** Proposed: a preference in the prompt. A stricter version would list only that herald while they are within range.
3. **Whether a new voice note needs your eye.** Proposed: it is shown in `pending` with the bounty and stored on approval.
