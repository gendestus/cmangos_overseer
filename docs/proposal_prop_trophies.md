# Proposal: story props for trophy bounties

Status: accepted and built 2026-10-04. Replaces sections 1.3 and 1.7 of `DEV_PLAN.md`.
Audience: whoever implements Phase 1 (currently Claude Code).

Every fact in section 3 was confirmed against the live server before building, by the
spike in section 6. Two results worth carrying forward:

- **A quest-only drop stops once the objective is met.** Better than assumed: a trophy
  bounty cannot flood a character with spares, so the expected-kills arithmetic in 4.6 is
  a worst case rather than an average.
- **Flag 2048 behaves as read.** One Kobold Vermin, two party members, each looted their
  own copy from the same corpse.

One limit the spike exposed that is not in this document: **purge cannot take a prop out of
a player's bag.** A loot table is restored exactly, but whatever was already looted stays
looted. The check in 4.6 that refuses a prop the character is already carrying is what
keeps that from corrupting the next bounty, and it earned its place the first time a test
bounty was purged mid-flight.

## 1. Problem

Section 1.3 said a trophy bounty may ask for any item nearby creatures drop at 30% or better. The live loot tables do not support that: Kobold Worker's best ordinary drop is Melted Candle at 29.55%, Defias Thug's is 13%. Under that rule trophies would be unofferable through most of the early game.

The fallback, stock quest-only drops, works mechanically but is weak as the main source:

- 776 of the 813 quest-only items already belong to a stock quest, so a trophy bounty re-runs an objective the player may have done.
- If a character holds that stock quest at the same time, both quests count the same items, and turning one in takes them from the other.
- The DM cannot choose what is collected, so it cannot write "the kobolds stole my books".

## 2. Decision

Make DM-owned **props** the main source for trophies:

1. A pool of generic story items is created once by custom-sql (one server restart).
2. To post a trophy bounty, the DM adds a prop to the target creature's loot table as a quest-only drop, reloads loot, then posts the quest.
3. When the bounty is retired, removed or expires, the loot row is deleted.

Stock quest-only drops stay available as a second source, with a guard (4.7).

## 3. Facts this rests on

Checked against mangos-classic 8ec338a and the live-schema world database. Re-check anything marked "confirm".

| Fact | Where |
|---|---|
| `reload creature_loot_template` exists and runs from the remote console | `Chat.cpp`, reload command table |
| Loot is rolled when the corpse is created, so a row added before the kill applies | `Creature.cpp`, `new Loot(looter, this, LOOT_CORPSE)` |
| A negative `ChanceOrQuestChance` means quest-only, with the magnitude as the percent | `LootStoreItem` constructor, `LootMgr.h` |
| A quest-only item drops for a player whose incomplete quest needs it, including a DM quest | `Player::HasQuestForItem` |
| An item with flag 0x800 (2048) can be looted by every eligible party member from one corpse | `LootMgr.cpp`, `freeForAll = Flags & ITEM_FLAG_MULTI_DROP` |
| Loot rows are keyed by the creature's `LootId`, which is not always its entry | `Loot::FillLoot(creatureInfo->LootId, ...)` |
| Loot tables are effectively per creature type: of 4,240 lootable templates, one loot id is shared by several | world database |
| New item templates load only at startup | no `item_template` entry in the reload table |
| A loot row's `item` is 32-bit, `maxcount` is 8-bit, `condition_id` is read as 16-bit | `LootStoreItem`, `LootMgr.h` |
| `creature_loot_template` has a `comments` column (300 characters) | live schema |

Rules the server applies to a loot row when it loads (`IsValidItemTemplate`): the item must exist, `mincountOrRef` must be at least 1, `maxcount` must not be below it, the chance must not be zero in group 0, and the group must be under 128. A row that fails is skipped with a log line.

## 4. Design

### 4.1 The prop pool (one-time, one restart)

A new file `server/dm_props.sql`, installed in the game server's `custom-sql` folder.

- **Ids:** 200000 to 200199, the range `ai_dm_spec.md` reserves for DM items.
- **How rows are made:** clone a stock quest item into a temporary table, edit, `REPLACE INTO item_template`, the same column-agnostic pattern `spell_books.sql` uses. Red Burlap Bandana (752) is a good base: class 12, quality 1, bonding 4 (quest item), stackable 20, no price.
- **Fields to set per prop:** `entry`, `name`, `displayid`, `Flags = 2048` (party loot), `stackable = 100`, `maxcount = 0`, `startquest = 0`.
- **Icons:** `displayid` is borrowed from a stock item. Verified donors are listed below; find others by name on the live database.
- **Discovery at run time:** the DM reads the pool with `SELECT entry, name FROM item_template WHERE entry BETWEEN 200000 AND 200199`. No list is hard-coded in Python.

Written: `server/dm_props.sql` creates 98 props in six blocks, each with room to grow. Every icon was checked against the live-schema database.

| Ids | Block | Count | Examples |
|---|---|---|---|
| 200000 to 200012 | Papers and records | 13 | Stolen Book, Coded Orders, Sealed Letter, Crude Map |
| 200020 to 200033 | Valuables | 14 | Stolen Silver, Looted Lockbox, Marked Insignia |
| 200040 to 200059 | Supplies and tools | 20 | Stolen Supplies, Miner's Pick, Stolen Candle, Crude Key |
| 200060 to 200087 | Taken from the body | 28 | Severed Paw, Bloodied Fang, Staring Eye, Torn Pelt |
| 200100 to 200118 | Relics and the uncanny | 19 | Carved Idol, Pinned Doll, Dark Rune, Vial of Poison |
| 200130 to 200133 | Growing things | 4 | Pale Mushroom, Gnarled Root |

### 4.2 Quest spec

Add an optional list to the spec `hot_quest.py` validates:

```json
"props": [
  { "item": 200003, "count": 12, "creature": 257, "chance": 50 }
]
```

- At most two entries. `count` 1 to 20. `chance` one of 100, 50, 34.
- Each entry renders as a collect objective (`ReqItemId`, `ReqItemCount`) and one loot row.
- `creature` is a creature entry. The renderer resolves it to `LootId` in SQL; it never assumes the two are equal.

### 4.3 SQL

Apply, for each prop, before the quest row is written:

```sql
INSERT INTO creature_loot_template
  (entry, item, ChanceOrQuestChance, groupid, mincountOrRef, maxcount, condition_id, comments)
SELECT t.LootId, 200003, -50, 0, 1, 1, 0, 'dm:30007 Stolen Book'
FROM creature_template t
WHERE t.Entry = 257 AND t.LootId <> 0
ON DUPLICATE KEY UPDATE
  ChanceOrQuestChance = IF(comments LIKE 'dm:%', VALUES(ChanceOrQuestChance), ChanceOrQuestChance),
  comments            = IF(comments LIKE 'dm:%', VALUES(comments), comments);
```

- The `comments` tag `dm:<quest id>` marks the row as the DM's. The update clause never changes a row that is not tagged.
- Preflight adds two checks per prop: the creature has a loot table, and no untagged row exists for that loot id and item.

Retire and remove both run:

```sql
DELETE FROM creature_loot_template WHERE comments LIKE 'dm:30007 %';
```

### 4.4 Console

- Add `reload creature_loot_template` to the allow-list in `console.py`.
- Order on apply: write loot rows, reload loot, write quest, `reload all_quest`, announce. The quest must not be on offer before the drop exists.
- Order on retire or remove: change the quest first, then delete loot rows and reload loot.

### 4.5 The model's form

For kind `trophy` the model chooses:

- `prop`: an item id from a "props you may use" list in the prompt.
- `target_creature`: from the existing nearby-creatures list.
- `count` and `chance`.

The prompt tells it the prop's name must make sense as something that creature would carry or have taken.

### 4.6 Checks before anything is written

| Check | Why |
|---|---|
| Prop is in the pool and creature is on the targets list | Same list discipline as every other choice |
| Expected kills, `ceil(count * 100 / chance)`, is at most the alive count and at most 24 | The area must be able to supply the hunt |
| No live bounty already uses this prop | A character holding bounty A would otherwise loot A's prop from bounty B's creature |
| The character is not already carrying the prop | Leftovers from an earlier bounty would count at once. Confirm the inventory tables: likely `character_inventory.item_template` |

Party loot is on for every prop, so a party does not multiply the kills needed.

### 4.7 Stock quest-only drops as the second source

Keep Claude Code's inversion for this source: quest-only stock drops first, ordinary drops at 30% or better as a last resort. Add one guard: never offer an item that a quest in the character's log already needs (`character_queststatus` with status 3 and not rewarded, joined to `quest_template.ReqItemId1` to `4`).

### 4.8 Memory and display

- `quests` in `state.db` gains a `props` column (JSON).
- `dm.py pending` and `dm.py story` show the prop, creature and drop rate.
- The story prompt's "earlier bounties" line names the prop, so the model can refer back to "the books".

## 5. Limits to design around

- **Only creatures with a loot table** can carry a prop. The target search already requires one.
- **The drop applies to every spawn of that creature type,** not only the ones near the character.
- **Creatures killed before the reload do not get the prop.** Corpses already on the ground are unaffected.
- **A world database rebuild wipes the loot rows,** as it wipes live quests. `state.db` has the spec; re-applying after a rebuild is the existing known gap.
- **Do not attach a condition to a loot row** unless its id is below 65536. This is the same 16-bit limit that broke the vendor filter.
- **Prop names are fixed until the next restart.** Bespoke names are out of scope here.

## 6. Spike before building (about 20 minutes)

Do this by hand to confirm the chain in benilla. It needs no pool: use any unused stock quest item as a stand-in (the database has 306 that nothing drops, sells or asks for).

1. Insert one quest-only loot row for Kobold Vermin with that item.
2. `reload creature_loot_template`.
3. Apply a copy of `test_quest.json` with a collect objective for the item.
4. Kill kobolds with the quest and without it. The item should appear only with it.
5. Repeat in a party of two with an item flagged 2048. Both should be able to loot it from one corpse.

## 7. Tests to add

Offline (`tests/test_offline.py`):

- Spec validation: bounds on `count` and `chance`, at most two props.
- Rendered SQL: resolves `LootId`, tags `comments`, never touches an untagged row, and retire deletes by tag.
- Expected-kills arithmetic, including the cap.
- One live bounty per prop.

With the stub model: a trophy bounty proposed, approved, turned in and retired, ending with no `dm:` rows left in the loot table.

## 8. Done when

- `server/dm_props.sql` is installed and the props exist in game (the file is written; it needs one restart).
- A trophy bounty with a prop can be proposed, approved, completed and retired on the live server.
- After retirement the creature's loot table is exactly as it was.
- Every check in 4.6 has a test.

## 9. Not in this proposal

- Bespoke, DM-named props created in a nightly batch.
- Seeded reward drops (dev plan 3.3). They can reuse the loot-row plumbing built here, with a positive chance and a real item.
