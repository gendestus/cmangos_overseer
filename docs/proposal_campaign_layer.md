# Proposal: campaign and zone chapters

Status: proposed 2026-10-07. Canon (4.5), dossier and chapters (4.2 to 4.4) and sealing (4.7) built 2026-10-07. A storytelling change; no new game mechanics.
Audience: whoever implements it (currently Claude Code).
Reference: the query in 4.2 is `world_query.zone_dossier`. It also filters by side (`RequiredRaces`), which the first prototype did not: unfiltered, an Alliance character in Hillsbrad would have been shown mostly Horde quests.

## 1. Problem

Mini-arcs chain: each is planned from how the last one ended. That gives a story that turns, but it has no destination and no sense of place.

- **No long view.** Nothing decides what a character's story is about across sixty levels, so arcs drift.
- **No sense of place.** An arc is tied to a level band, not a zone. It does not know Westfall's own story, who lives there, or that the character has moved on to Redridge.
- **Nothing remembers what was established.** A fact one bounty invents ("the ledger named a noble") lives only in a 120-word summary that is rewritten every time.

## 2. Decision

Add two planning layers above the mini-arc, and one memory beside them.

| Layer | Scope | Written | Holds |
|---|---|---|---|
| **Campaign** | One character, whole life | When first noticed | What the story is about, in acts |
| **Zone chapter** | One character in one zone | When they settle there | How the current act plays out in this place |
| Mini-arc (exists) | A few levels, now inside one zone | From the chapter's next seed | Two to four beats |
| Bounty (exists) | One quest | When none is out | The quest |
| **Canon** | Server-wide and per character | By every layer | Facts the story has established |

Three choices shape the design:

**A. The campaign is a spine, not a script.** It fixes a premise, a question and the intent of each act. It does not fix events. A plan of events written at level 1 will be wrong by level 20, because the player chooses where to go and what to do.

**B. A chapter is grounded in the zone's own story.** The world database already files every stock quest under its zone. That list is the zone's canonical story and cast, and it goes into the prompt.

**C. Coherence comes from memory, not only from planning.** More planning layers do not stop one bounty contradicting another. A ledger of established facts does.

## 3. Facts this rests on

Checked against the live-schema world database.

| Fact | Detail |
|---|---|
| A character's zone | `characters.zone`, the same id stock quests are filed under |
| A zone's stock quests | `quest_template.ZoneOrSort = <zone id>` |
| Zones with ten or more stock quests | 69 |
| A dossier's size as prompt text | About 1,300 characters |
| Spawns carry no zone | Still true. The dossier comes from quests, not from spawn positions |

What the prototype returns for Westfall, trimmed:

> 35 stock quests, levels 9 to 44.
> Its quests, in level order: Furlbrow's Deed; Westfall Stew; ... The People's Militia; ... Red Leather Bandanas; The Defias Brotherhood; The Coast Isn't Clear ...
> Who gives them: Grimbooze Thunderbrew (6); Gryan Stoutmantle <The People's Militia> (6); Captain Grayson (3); ... Master Mathias Shaw <Leader of SI:7> (1); The Defias Traitor (1).
> What they send players against: Defias Smuggler (11-12); Defias Trapper (12-13); Murloc Coastrunner (12-13); Harvest Watcher (14-15); ...

The model knows Azeroth's lore well. The dossier anchors that knowledge to what this server actually contains.

## 4. Design

### 4.1 The campaign

One per character, in a new `campaigns` table.

| Field | Content |
|---|---|
| `premise` | Two sentences: what the Overseer sees in this character |
| `question` | The one question the story asks of them ("Will the hunter become what he hunts?") |
| `stake` | What the Overseer itself wants from the outcome |
| `acts` | Three to five, each with a level band, an **intent** in one sentence, and the thread of Azeroth's story it leans on |
| `cast` | Three to six key players, each a canonical figure or faction with a role in this story |
| `reveals` | Two or three things to be learned, each pinned to an act, not to an event |
| `current_act` | Index into `acts` |

Written by a model call when the character is first noticed. This replaces "write an arc at first notice"; the first arc now comes from the first chapter. Input: race, class, level, starting zone, the chronicle so far, the capstone books their class can use, one line on each other character's campaign, and any direction from the owner.

Rules for the model:

- Acts state intent, never events. "He learns the bandits are paid from inside the city", not "he kills VanCleef".
- An act names a thread of the world's story as it stands in 1.12, and the zones where that thread runs for this character's faction. It does not assume the character will go there.
- Cast members must be real figures or factions of that era.

**Act review.** When the character's level leaves the current act's band, one call reads what happened and may rewrite the remaining acts. It records an outcome for the finished act, the same way arcs do now.

### 4.2 The zone dossier: `world_query.zone_dossier(zone)`

Built from stock quests with `ZoneOrSort` equal to the zone and `entry < 30000`:

- Quest count and level range.
- Quest titles in level order, deduplicated, up to 24.
- Who gives them, most quests first, up to 14, with creature ids. These are natural heralds for the chapter.
- What they send players to kill, by level, up to 16, with notable creatures marked.

Dossiers are static, so cache them in `state.db` on first use.

### 4.3 The zone chapter

One per character per zone, in a new `chapters` table.

| Field | Content |
|---|---|
| `premise` | How the current act's intent plays out in this place |
| `local_cast` | Up to four creature ids from the dossier to favour as heralds, and the local adversary from the dossier's enemies |
| `seeds` | Two to four one-line ideas for mini-arcs, in order |
| `finale` | What ends the chapter: usually a named creature or the zone's dungeon |
| `hooks` | One line on where the story could go next |
| `status` | active, paused, finished |

**When one is written.** A character is settled in a zone when all of these hold:

- Their zone id has been the same for `DM_SETTLE_TICKS` consecutive ticks (default 3).
- The zone has a dossier with at least ten quests.
- The zone is not a capital city. Cities are interludes: the current chapter stays in force.

**Leaving and returning.** Settling elsewhere pauses the old chapter. Coming back resumes it. A chapter finishes when its finale bounty is turned in or its seeds run out.

### 4.4 Changes to mini-arcs

- An arc belongs to a chapter and is planned from that chapter's next seed.
- An arc's adversary must come from the dossier's enemies or the chapter's finale.
- A new end reason, `left`: the character settled in another zone. The existing reasons stay.
- `ARC_REACH` still limits the level bands.

### 4.5 Canon

A new table:

```sql
CREATE TABLE IF NOT EXISTS canon (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
    guid INTEGER,          -- the character it concerns, or NULL for the whole server
    zone INTEGER, creature INTEGER,      -- where and whom it concerns, when known
    fact TEXT,             -- one sentence, at most 25 words
    source TEXT,           -- 'bounty 30012', 'arc 7', 'chapter 3', 'owner'
    status TEXT NOT NULL DEFAULT 'active'   -- active, retired
);
```

- Every layer's form gains `canon_add`: up to two facts this piece of writing established. Facts, not plans: "Deputy Willem knows the kobolds were paid", never "the Overseer intends".
- A prompt is shown the active facts for this character, plus server-wide facts tagged with the current zone or any herald on the list, newest first, capped at ten.
- Owner commands: `dm.py canon [Character]` to list, `dm.py canon --retire <id>` to withdraw one, `dm.py canon --add "..."` to state one.

This is what lets different characters' stories touch without contradicting each other.

### 4.6 What each prompt carries

The layers must not bloat the bounty prompt. Budget:

| Section | Limit |
|---|---|
| Campaign: premise, question, current act's intent | 70 words |
| Chapter: premise, finale, current seed | 80 words |
| Arc (as now) | as now |
| Canon | 10 lines |
| Story so far (as now) | 120 words |

The dossier goes to the chapter and arc calls only, never to a bounty call.

### 4.7 Spoilers and approval

The owner plays on this server, and a campaign is the largest spoiler there is.

- New proposal types: `campaign`, `act_review`, `chapter`.
- New setting `DM_SEALED`: proposal types that are approved automatically and whose text `dm.py pending` and `dm.py arc` do not print without `--reveal`. Suggested: `campaign,act_review,chapter,arc`.
- `DM_PLAN_MODEL`: the model for campaign, review and chapter calls. These are rare and benefit from the strongest model; bounties keep `DM_MODEL`.

### 4.8 Characters that already exist

On the first tick after the upgrade, a character with history gets a campaign written from their chronicle and current arc. The current arc stays in force until it ends; the first chapter is written when they next settle.

## 5. Cost

Per character, over a whole life: one campaign, about five act reviews, and one chapter per zone settled in, perhaps twenty. Against the bounties and arcs already being written, this is small.

## 6. Risks

- **The plan fights the player.** Mitigated by acts being intent, by chapters being written only once the character is somewhere, and by act review. If a character ignores the story entirely, the campaign should be allowed to become about that.
- **Plot-speak.** More planning above can make the text below read like a synopsis. The bounty rules must keep saying: concrete errand, the herald's voice, no summary of the plan.
- **Lore errors.** The model may misremember who is where. Only creatures on a dossier or a nearby list may be given a part to play in game; lore mentioned in passing is not checked.
- **Zone boundaries.** The chapter follows `characters.zone`, which the server updates on save. A character hunting along a border may flicker; `DM_SETTLE_TICKS` damps that.

## 7. Build order

1. **Canon** (4.5). Small, independent, and improves what exists today.
2. **Dossier and chapters** (4.2 to 4.4). The largest visible change: arcs gain a place.
3. **Campaign and act review** (4.1). Gives chapters their direction.
4. **Sealing and the plan model** (4.7).

Steps 2 and 3 can ship in either order. A chapter without a campaign uses the last arc's outcome as its direction.

## 8. Tests to add

Offline:

- `zone_dossier` shapes its output and respects its limits, from a hand-built set of quests.
- Settling: three steady ticks trigger a chapter; a city does not; a zone with too few quests does not.
- Leaving pauses a chapter and ends its arc with `left`; returning resumes it.
- An arc whose adversary is not in the dossier is rejected.
- Canon: selection by character, zone and herald; the ten-line cap; a retired fact is not shown.
- A sealed proposal is approved without printing its text.

With the stub model: first notice writes a campaign; settling writes a chapter; the arc that follows cites the chapter's first seed.

## 9. Done when

- A new character gets a campaign, then a chapter for their starting zone, then an arc from that chapter.
- Moving from Elwynn to Westfall and staying produces a Westfall chapter whose cast and adversary come from Westfall's dossier.
- A fact established in one bounty appears in the prompt for a later one, including a bounty for a different character in the same zone.
- With sealing on, the owner can run the DM without reading any plan.

## 10. Decisions for the owner

1. **How many acts.** Proposed three to five, by level band. Fewer acts mean a looser spine.
2. **Sealing.** Whether campaigns and chapters should be hidden from you by default.
3. **Cities.** Proposed as interludes with no chapter of their own. The alternative is a Stormwind chapter of intrigue.
4. **Shared story.** How much one character's campaign should see of another's. Proposed: one line each, plus shared canon.
