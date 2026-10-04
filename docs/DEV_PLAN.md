# Overseer dev plan: richer bounties, mail, rewards, throughlines

Status: decisions taken 2026-10-04. Phase 0 (including 0.4), the first half of Phase 4 and dynamic heralds are built; Phases 1 to 3 are not.
Order follows the owner's ranking. Estimates are rough guesses for one person working with a coding agent.

| Phase | Result | Estimate | Status |
|---|---|---|---|
| 0 | One-bounty rule made explicit; typed proposals; offline tests | An evening | Built |
| 4a | Mini-arcs: written at first notice, carried into every bounty, ended and succeeded | Pulled forward | Built |
| G | Dynamic heralds: the model picks a nearby friendly quest NPC; no configured list | An evening | Built |
| 0.4 | Narrow database users, a kill switch, and one command that undoes everything | An evening | Built |
| 1.1 | Target search v2: ranks, two distance tiers, bearings, party awareness | An evening | Built |
| 1 | The rest of richer bounties: six kinds, chains, spell rewards | Two weekends | Next |
| 2 | Overseer mail, both directions | A weekend | |
| 3 | Extra rewards with a budget | A weekend | |
| 4b | Arc revision, grounding the adversary, signature rewards | An evening or two | |

## Decisions

1. **A bounty nobody accepts** still expires after `DM_STALE_HOURS` (24).
2. **Letters send without approval.**
3. **Reward budgets** start as rough numbers and get tuned in play.
4. **The goal is a fully automatic DM.** Approval is a setting per proposal type, `DM_AUTO_APPROVE`, so turning automation on later is a configuration change, not a rewrite. It defaults to `letter`.
5. **Arc setup was pulled forward** and is built.
6. **Arcs are mini-arcs** (decided 2026-10-04): each covers about four to eight levels with two to four beats. It ends when its finale bounty is turned in or the character out-levels it, and the next is planned from how it actually ended.
7. **The spec's own guardrails come before new content** (decided 2026-10-04). Reading `ai_dm_spec.md` back against the code turned up three things it asks for that the plan had dropped: narrow database users (spec section 3), a kill switch and a purge script (section 6). They became Phase 0.4 and were built first, because Phases 1 to 3 each widen what the DM writes.
8. **Quest givers are chosen, not configured** (decided 2026-10-04). `DM_GIVERS` is gone. Friend or foe is read from the server's extracted faction files, which also replaces the old hostility heuristic for targets.

## Ground rules (unchanged)

- Nothing reaches the game without approval.
- The model fills in a form. It never writes SQL or console commands.
- Every model choice is checked against lists the script built from the live database.
- Console commands go through the allow-list in `console.py`.
- The DM reaches the database as two narrow users, never as root (0.4).
- One flag stops everything; one command undoes everything (0.4).

## Phase 0: one active bounty, and shared plumbing (built)

### 0.1 The one-bounty rule

The loop already refuses to propose while a character has a bounty that is offered or accepted, or a proposal waiting. The one exception today: a bounty that is offered but never accepted is dropped after `DM_STALE_HOURS` (24), and a new one may then be proposed.

Changes:

- State the rule in the README and in `dm.py`'s docstring.
- An accepted bounty never expires. (Already true; add a test.)
- Decide what happens to a bounty that is never accepted. See open decision 1.
- A chain (1.4) counts as one bounty: no new proposal while any step is open.

### 0.2 Proposals become typed

Phases 2 to 4 add things to approve that are not bounties. Generalise the `proposals` table with a `type` column: `bounty`, `letter`, `gift`, `arc`, `arc_revision`. `dm.py pending`, `approve` and `reject` handle every type.

### 0.3 A test harness in the repo

Add `tests/` with canned model answers and a script that runs the loop with `DM_LLM_PROVIDER=stub` against a throwaway `state.db`. Every phase below adds fixtures here.

## Phase 0.4: the spec's guardrails (built)

### 0.4.1 Two narrow database users

`db_users.py` creates `dm_read` and `dm_write` and points `DM_DB_QUERY_COMMAND` and `DM_DB_COMMAND` at them, so no script speaks to the game as root any more.

- The reader gets SELECT on the world and characters databases and nothing else.
- The writer gets SELECT on the four tables the preflight reads, INSERT and DELETE on `quest_template` and the two giver link tables, and SELECT plus DELETE on `character_queststatus`. MariaDB needs the SELECT there because a DELETE's WHERE clause reads a column.
- `--check` proves it both ways: each user can do its job, and the writer is refused the accounts database, a player's mail, and any change to a creature or an item. Fourteen probes, all passing.
- An offline test compares the writer's grants against the tables the renderer actually writes, so a later phase that adds a table fails a test instead of failing in play.

Still root: `db_users.py` itself, which creates the users. It reads the root password inside the container and never handles it.

### 0.4.2 The kill switch

`dm.py pause "why"` writes a flag file; `console.paused()` is the single source of truth and every path to the game checks it: `console.run`, `apply_quest.run_sql`, `dm.tick`, `dm.approve` and `write_quest`. `DM_PAUSE=1` does the same from the environment. Reading is deliberately unaffected, so the chronicle still reads while the DM is off. The allow-list still refuses a forbidden command first, so pausing never widens anything.

### 0.4.3 Purge

`dm.py purge` enumerates every quest in 30000 to 39999 **in the world database**, not in `state.db`, then removes each one with its giver links and every character's record of it. Reading the live database matters: the first run found quest 30000 live and unknown to the DM's memory.

Removal needs only an id (`hot_quest.render_remove_by_id`), so a quest whose spec is lost or whose title would fail the authoring rules is still cleanable. The id range check still applies, so purge cannot touch a stock quest. The DM's own memory is kept and the quests are marked `purged`.

Not covered, and worth knowing: mailed items cannot be recalled, and a purge erases a player's record of having completed a DM quest, so the same quest could be offered again. Phase 2's letters and Phase 3's gifts will need their own undo.

## Phase 1: richer bounties

Today every bounty is "kill N of one normal creature within 400 yards".

### 1.1 Target search v2 (`world_query.targets`)

Built. `targets()` is one window-function query that returns the nearest spawn of each eligible creature with its rank, both counts, distance and bearing.

- **Ranks.** `creature_template.Rank`: 0 normal, 1 elite, 2 rare elite, 4 rare. A world boss (3) is never offered. Each rank has its own level window and its own threshold for how many must be alive: a hunt of several needs three, a named creature needs one. A single spawn is flagged `unique`, so quest text can treat it as an individual.
- **Two tiers.** `near` is within 400 yards, `far` reaches 1,500 on the same continent. `trim()` keeps the list short without letting the far tier crowd out what is nearby.
- **Bearings.** Distance and compass bearing of the nearest spawn, from `bearing()`. The prompt marks a far target "a journey" and the rules tell the model to name the direction.
- **No repeats.** `dm.recent_targets` reads the creature ids out of the last two bounties' specs and `gather(skip=...)` leaves them out. The ids come from the spec, not the `target` column, which holds only a name.
- **The party.** `parties()` feeds `targets(party=...)`. An elite is offered only to a character with online company, or one `SOLO_ELITE_MARGIN` (4) levels above it; those offered on the party's strength are flagged `needs_party`, which 1.2's validator should re-check at approval time, because a party can disband in between.

Also relaxed: the `LootId <> 0` filter now applies only to normal creatures. A named elite with no loot table of its own is still worth facing.

Accepted: `world_query.py Gendestus` lists ranked, far and named targets with bearings. Verified against the live world at two levels — a level 4 in Northshire gets eleven normal targets across both tiers, a level 12 in Goldshire gets five rares solo and two party-gated elites (Felinni, Hogger).

### 1.2 Bounty kinds

The model picks a `kind`; each kind has its own checks.

| Kind | Objective | Check |
|---|---|---|
| hunt | Kill N of a normal creature (today's bounty) | N within alive count and cap |
| mark | Kill one named, rare or elite creature | Target alive; elite only with a party or a level margin |
| trophy | Collect N of an item nearby creatures drop | Expected kills within alive count and cap |
| trial | A hunt against a timer | Time limit within set bounds |
| journey | A hunt or mark in the far tier | Distance stated in the text |
| party | Two objectives, written for the group | Character is in a party |

Up to two objectives per quest to start; the quest format allows four.

### 1.3 Trophy objectives from stock loot

- New query: items dropped by eligible nearby creatures, with drop chance.
- Offer only items with a drop chance of 30% or more, or quest-only drops. Quest-only drops appear for any quest that needs the item, including a DM quest (verified in the loot code).
- The validator computes expected kills (count divided by chance) and rejects a hunt the area cannot supply.
- Prefer kill objectives for parties: most stock items give one copy per corpse.

### 1.4 Chains

- New spec field `requires_quest`, written to the quest's `PrevQuestId`.
- A follow-up is then offered only to characters who finished the earlier quest, which also stops other players seeing a story step meant for one character.
- Retired quests stay in the quest table, so the link stays valid.
- State: add `chain_id` and `step` to `quests`.

### 1.5 A spell as the reward

- `hot_quest.py` already supports this. Add a `reward_spell` choice to the model's form, drawn from the capstone list.
- Checks: the character does not know it, and is at or above the level the book would require.

### 1.6 Timed trials

- New spec field `time_limit_minutes`, written to `LimitTime`. The server treats a quest with a time limit as timed when it loads (seen in the loader).
- Spike first: confirm the timer shows in benilla and that failing it behaves sensibly.

### 1.7 DM-owned trophy items (optional, needs one restart)

A pool of about 30 generic items ("Marked Insignia", "Strange Idol") created once by custom-sql. The DM then adds one to a creature's loot live (`creature_loot_template` is hot-reloadable) as a guaranteed quest-only drop that every party member can loot. This removes the drop-chance problem in 1.3.

### 1.8 Prompt, form, display

- Tool schema: `kind`, up to two objectives, `requires_quest`, `reward_spell`, `time_limit_minutes`.
- XP and money caps scale by kind.
- `dm.py pending` and `story` show the kind and chain position.

## Phase 2: Overseer mail

### 2.1 Outbound letters

- New module `mailer.py` over the console's `send mail`, `send items` and `send money`.
- Console mail arrives on GM stationery with no sender name (the sender id is 0 in the server code), so every letter signs itself.
- Spike first: how quotes and line breaks in the body survive the command. The console rejects raw line breaks, so a line-break code has to be found or letters kept to one paragraph.

### 2.2 Reactions

A second, smaller model call writes a letter when something notable happens.

- Triggers: a bounty turned in, a bounty ignored, a level milestone, two players first seen grouped.
- Throttle: one letter per character per set number of hours.
- Letters are proposals of type `letter`. A setting can let gift-free letters send without approval.

### 2.3 Inbound letters

The reading code exists; it needs a recipient.

- Manual setup: log in once on the OVERSEER account and create a character named for the DM. Set `DM_MAIL_CHARACTER`.
- If players are on both factions, set `AllowTwoSide.Interaction.Mail = 1` in `mangosd.conf`. It is 0 by default, which blocks cross-faction mail. This is the one restart in this phase.
- A new letter triggers a reply proposal and is included in the next bounty's context.
- Attachments from players are ignored.

### 2.4 Player text is untrusted

A letter is the first place a player's own words reach the model. Treat it as in-fiction speech: it can steer tone and requests, but it cannot grant anything. The reward budget (3.1) and the validator's lists apply regardless of what a letter asks for.

### 2.5 State

New table `letters`: time, character, direction, subject, body, gift, status. The chronicle and the model's context show the last three exchanged.

## Phase 3: extra rewards

### 3.1 Ledger and budget

- New table `rewards`: time, character, what, why, and which quest or letter delivered it.
- One budget for every channel: gold per day, items per week, and capstones per level span, by level band.
- The validator checks the budget for quest rewards, mailed gifts and seeded drops alike.

### 3.2 Reward catalogue

Extend `world_query.reward_items` into a catalogue with a budget cost per entry:

- Capstone books (exists).
- Other classes' vendor books, as a minor boon.
- Stock gear the class can use, at the character's level, from the item table.
- Consumables by level.

### 3.3 Delivery

- **Quest reward with a choice:** up to six items, player picks one. `hot_quest.py` already supports choice items.
- **Gift by mail:** through Phase 2.
- **Seeded drop:** add an item to one creature's loot, live, and remove it once it has dropped or expired.

### 3.4 Unprompted gifts

Triggers: level milestones, helping another player's bounty, returning after a long absence, an arc beat (Phase 4).

## Phase 4: character throughlines

Built so far (4a): 4.1, 4.2 and 4.3, as mini-arcs with succession. Remaining (4b): mid-arc revision (4.4), grounding the adversary (4.5), and delivering the signature reward through the Phase 3 catalogue. Until then, a finale bounty can offer the signature reward only when the book is on that bounty's reward list.

### 4.1 The arc

New table `arcs`, one active arc per character:

- **Premise:** one or two sentences.
- **Lure:** what the Overseer wants this character to become ("lead this priest down a dark path").
- **Adversary:** the enemy group the story turns toward ("the Naga").
- **Beats:** two to four planned steps, each with a level band and an intent, all within about ten levels of the character.
- **Signature reward:** one capstone book the arc builds toward, chosen from the books that character's class can use. Warriors, rogues and hunters cannot use the books, so their arcs have none.

The arc is never shown to players.

### 4.2 Laying it down

- When a character is first noticed, a model call writes an arc from their race, class, level and the capstones their class could borrow.
- The owner can seed it: `dm.py arc <Character> --seed "lead this priest down a dark path"`.
- An arc is a proposal of type `arc` and needs approval before any bounty uses it.
- Characters the DM already knows get an arc written from their chronicle.

### 4.3 Using it

- Every bounty and letter prompt carries the premise, the current beat and the next one.
- The model reports `beat_progress`: advance, hold, or detour, with a reason.
- Signature rewards come from the Phase 3 catalogue, filtered by the arc.

### 4.4 Revising it

When the player's actions contradict the arc (refusing the dark path, out-levelling a beat), the model may propose a revision. Revisions need approval.

### 4.5 Grounding the adversary

- New query `find_creatures(name pattern, level range)`: where matching creatures spawn, with distance and bearing from the character.
- Limit: the world database has no zone for a spawn, only coordinates. The model knows from lore which zones hold Naga; the query can confirm they exist at the right levels on this continent and how far away, but cannot name the zone.
- This depends on the far tier and bearings from 1.1.

## Dependencies

- 1.1 is the base for most of Phase 1 and for 4.5.
- Phase 3's gift delivery needs Phase 2.
- Phase 4's signature rewards need Phase 3's catalogue.
- Arc setup (4.1 to 4.3) was pulled forward, so every bounty written in Phases 1 to 3 already has a direction.

## Spikes before building

| Spike | Answers |
|---|---|
| Chain a second quest behind quest 30000 with `requires_quest`, reload, check who is offered it | 1.4 |
| Timed quest in benilla | 1.6 |
| `send mail` with quotes and a line-break code; how the letter looks in game | 2.1 |
| Mail a letter to the DM character and read it back | 2.3 |
| Add an item to a creature's loot, reload, kill it | 1.7, 3.3 |
