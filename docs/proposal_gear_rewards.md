# Proposal: gear rewards, with rare prizes for significant bounties

Status: implemented 2026-10-06 (see `world_query.gear`, `dm_state.gear_budget`). Proposed 2026-10-05. Implements section 3.2 of `DEV_PLAN.md` and the smallest useful part of 3.1.
Audience: whoever implements it (currently Claude Code).
Reference: the prototype of the query and filters now lives in `world_query.gear` and `world_query.fit_gear`.

## 1. Problem

Ordinary bounties pay only money. `world_query.reward_items` returns capstone spell books and nothing else, and the model's rules tell it a capstone is a rare prize. So a character sees coin until an arc's finale.

The owner wants:

- Gear the character can actually use as a reward on ordinary bounties.
- The Overseer able to step that up to a rare item when a bounty is significant, so that killing a notable creature can pay a blue weapon the character could not otherwise get at that place or time.

## 2. Decision

Add a gear catalogue with two tiers, alongside the capstone list, which is unchanged.

| Tier | Quality | When the model may offer it | Power window |
|---|---|---|---|
| Standard | Uncommon | Any bounty, within a frequency limit | Up to 4 levels below the character, never above |
| Prize | Rare | Only a significant bounty, within a budget | From 2 below to `DM_PRIZE_REACH` levels above |

A bounty is **significant** when its kind is `mark`, when it is a `journey` or `party` bounty that includes a mark, or when it concludes an arc.

## 3. Facts this rests on

Checked against mangos-classic 8ec338a and the live-schema databases.

| Fact | Where |
|---|---|
| The skill an item needs comes from its class and subclass, by two fixed tables | `Item::GetSkill`, `Item.cpp` |
| A character's weapon and armor skills are rows in `characters.character_skills` (`guid`, `skill`, `value`, `max`) | live schema |
| What a character holds, equipped, in bags or banked, is in `characters.character_inventory` (`guid`, `item_template`) | live schema |
| Quests can offer a pick of up to six items, and `hot_quest.py` already renders `reward.choice_items` | `hot_quest.py` |
| `ItemLevel` is unsigned: subtracting from it without a cast is an out-of-range error | found while testing |
| The whole query takes about 0.3 seconds | test database |

Gear that qualifies under the rules in 4.1, by the level needed to use it:

| Levels | Uncommon | of which weapons | Rare | of which weapons |
|---|---|---|---|---|
| 1 to 10 | 78 | 27 | 0 | 0 |
| 11 to 20 | 270 | 77 | 94 | 48 |
| 21 to 30 | 240 | 48 | 156 | 76 |
| 31 to 40 | 209 | 16 | 163 | 72 |
| 41 to 50 | 177 | 7 | 222 | 83 |
| 51 to 60 | 164 | 8 | 711 | 163 |

**No rare gear exists below power level 15.** There are 11 rare items at 15 (6 of them weapons), 7 at 16, 10 at 17 and 29 at 18. So a character sees prizes only from level `15 - DM_PRIZE_REACH` (see 4.3).

## 4. Design

### 4.1 The catalogue: `world_query.gear(who, tier)`

**An item's power level.** Many quest rewards have no level requirement, so the window cannot use `RequiredLevel` alone:

```sql
IF(i.RequiredLevel > 0, i.RequiredLevel, GREATEST(1, CAST(i.ItemLevel AS SIGNED) - 5))
```

**Candidates, in SQL:**

```sql
SELECT ...                                   -- entry, name, class, subclass, InventoryType, RequiredLevel,
                                             -- the power level above, stat_type1..10, stat_value1..10
FROM item_template i
JOIN (SELECT item FROM creature_loot_template
      UNION SELECT item FROM reference_loot_template
      UNION SELECT item FROM gameobject_loot_template
      UNION SELECT RewItemId1 FROM quest_template UNION SELECT RewItemId2 FROM quest_template
      UNION SELECT RewChoiceItemId1 FROM quest_template   -- ... through RewChoiceItemId6
      UNION SELECT item FROM npc_vendor) real_item ON real_item.item = i.entry
WHERE i.entry < 90000                        -- stock items only
  AND i.class IN (2, 4)                      -- weapons and armor
  AND i.Quality = :quality                   -- 2 standard, 3 prize
  AND i.RandomProperty = 0                   -- fixed stats only
  AND <power level> BETWEEN :low AND :high
  AND (i.AllowableClass = -1 OR (i.AllowableClass & :class_mask) <> 0)
  AND (i.AllowableRace  = -1 OR (i.AllowableRace  & :race_mask)  <> 0)
  AND i.RequiredSkill = 0                    -- no profession-only gear
  AND i.requiredhonorrank = 0 AND i.RequiredReputationFaction = 0
  AND i.InventoryType NOT IN (0, 4, 18, 19, 24, 27, 28)   -- not shirts, bags, tabards, ammo, quivers, relics
  AND NOT EXISTS (SELECT 1 FROM characters.character_inventory ci
                  WHERE ci.guid = :guid AND ci.item_template = i.entry);
```

The `real_item` join keeps out developer and test items: an item qualifies only if something in the world drops it, sells it or rewards it.

**Then, in Python:**

1. **Can they use it?** Map the item to a skill and require it in the character's skills.
   - Weapons, by subclass: `{0: 44, 1: 172, 2: 45, 3: 46, 4: 54, 5: 160, 6: 229, 7: 43, 8: 55, 10: 136, 13: 162, 15: 173, 16: 176, 18: 226, 19: 228}`
   - Armor, by subclass: `{1: 415, 2: 414, 3: 413, 4: 293, 6: 433}` (cloth, leather, mail, plate, shield)
   - Because this reads the character's own skills, it follows the server's added race and class combinations with no class table.
2. **Body armor: only the heaviest type they wear.** A mail wearer is not offered cloth robes. Cloaks (inventory type 16), rings, necklaces and trinkets are exempt.
3. **Stat fit.** Drop an item whose largest stat is no use to the class. The prototype's table, to tune in play:

   | Class | Stats that count |
   |---|---|
   | Warrior, Rogue | Strength, Agility, Stamina |
   | Hunter | Agility, Stamina, Intellect |
   | Paladin | Strength, Stamina, Intellect, Spirit |
   | Priest, Mage, Warlock | Intellect, Spirit, Stamina |
   | Shaman, Druid | all five |

   An item with no stats at all passes.
4. **Variety.** Sort weapons first, then by power level, and keep one item per kind (one sword, one chest piece), up to 8 standard or 6 prize.

**What the prototype offers a level 16 paladin with the defaults:**

- Standard: Blackrock Mace, Buzz Saw, Heavy Gnoll War Club, Burnished Boots, Leggings and Shield.
- Prize: Twisted Sabre, Diamond Hammer, Smite's Mighty Hammer, Deep Fathom Ring, Dreamsinger Legguards.

Several prizes are dungeon boss drops a few levels ahead, which is the effect the owner asked for.

### 4.2 The model's form

Add one field to the tool:

```json
"reward_gear": { "type": "array", "items": { "type": "integer" }, "maxItems": 3,
                 "description": "Item ids from the gear list. One is a fixed reward; two or three let the player pick." }
```

- One id renders as `reward.items`; two or three render as `reward.choice_items`.
- All ids must come from one tier. A mixed list is rejected.
- `reward_item` (the capstone field) stays. A bounty may carry a capstone or gear, not both.

The prompt gains a gear section, shown only for tiers the bounty may use:

```
Gear this character can use (id, name, kind, level to use it, stats):
Standard:
- 1296: Blackrock Mace, mace, level 16, +3 Strength
Prizes (a significant bounty only):
- 7230: Smite's Mighty Hammer, two-handed mace, level 18, +11 Strength, +4 Agility
```

And rules, added to `RULES` in `write_quest.py`:

- Gear is a reward for effort, not for every errand. Most bounties still pay coin.
- Offer a prize only when the list shows one, and make it belong to the story: the mark carried it, guarded it or stole it. Name it in the briefing.
- When offering a choice, offer different kinds of item, not three swords.

### 4.3 Settings

| Setting | Default | Meaning |
|---|---|---|
| `DM_GEAR_EVERY` | `2` | At most one gear reward in this many consecutive bounties for a character |
| `DM_PRIZE_LEVEL_SPAN` | `4` | A character must gain this many levels between prizes |
| `DM_PRIZE_REACH` | `5` | How many levels above the character a prize may be |

These are starting numbers to tune in play. With a reach of 5, prizes first appear at level 10, and they are thin until about level 13. A reach of 10 brings them in from level 5, and lets a level 10 character be offered Cruel Barb or Smite's Mighty Hammer to grow into.

### 4.4 Checks before anything is written

| Check | Why |
|---|---|
| Every gear id is on the list shown to the model, and all from one tier | List discipline, as for every other choice |
| Prize tier only on a significant bounty | The rule in section 2 |
| Standard gear respects `DM_GEAR_EVERY`; a prize respects `DM_PRIZE_LEVEL_SPAN` | The budget |
| Not both a capstone and gear | One headline reward per bounty |
| Money cap is halved when gear is given | Gear replaces part of the coin |

When the budget forbids a tier, leave that tier out of the prompt entirely. The model then never picks something the validator would refuse.

### 4.5 Ledger

A new table in `state.db`:

```sql
CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, guid INTEGER, quest INTEGER,
    tier TEXT,            -- standard, prize, capstone
    items TEXT,           -- JSON list of item ids offered
    level INTEGER,        -- the character's level when it was posted
    status TEXT NOT NULL DEFAULT 'posted'   -- posted, collected, lapsed
);
```

- Written when a bounty is approved; set to `collected` when the intended character turns it in.
- Set to `lapsed` when a bounty is ignored or removed, which frees the budget it used.
- The budget checks in 4.4 read `posted` and `collected` rows.

This is the ledger from plan section 3.1, limited to quest rewards. Mailed gifts and seeded drops can write to the same table later.

### 4.6 Memory and display

- `dm.py pending` shows the gear by name, with the tier.
- The "earlier bounties" lines in the story prompt name the gear a bounty paid, so the model can refer back to "the hammer you took from Smite".

### 4.7 Database access

The DM's read-only user needs `SELECT` on `characters.character_skills` and `characters.character_inventory`. Update `db_users.py`.

## 5. Limits to design around

- **No prizes below level `15 - DM_PRIZE_REACH`.** This is the data, not a bug: the game has no rare gear weaker than that.
- **Random-suffix items are excluded** ("of the Bear"). Their stats are rolled when the item is created, so the model could not describe them.
- **The stat-fit table is a heuristic.** It will occasionally pass an odd item or drop a good one. Approval is the backstop until the DM is automatic.
- **A level requirement above the character means "to grow into".** The prompt line shows the level so the model can say so.
- **Unique items are allowed.** The inventory check already excludes anything the character holds.

## 6. Tests to add

Offline (`tests/test_offline.py`), with a canned gear list:

- A gear id not on the list is rejected; a mixed-tier list is rejected.
- One id renders as a fixed reward, three as a choice.
- A prize on a `hunt` bounty is rejected; on a `mark` it passes.
- `DM_GEAR_EVERY` and `DM_PRIZE_LEVEL_SPAN` are enforced from ledger rows, and a `lapsed` row frees the budget.
- The money cap halves when gear is present.
- The skill, armor-weight and stat-fit filters, each on a small hand-built item.

With the stub model: a mark bounty proposed with a prize, approved, turned in, and the ledger row ends `collected`.

## 7. Done when

- A standard gear reward appears on an ordinary bounty and can be collected in game.
- A `mark` bounty can offer a rare weapon named in its briefing.
- Two bounties in a row cannot both pay gear with the default settings.
- `python3 world_query.py <Character>` prints both gear lists.

## 8. Not in this proposal

- Gear by mail or as a seeded drop (plan 3.3).
- Consumables and other classes' vendor books (the rest of plan 3.2).
- Random-suffix items.
- Epic quality.
