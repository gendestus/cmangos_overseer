-- =============================================================================
-- class_quest_unlock.sql
-- Companion to all_race_class_combos.sql for CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/class_quest_unlock.sql
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- Part A: class quests locked to specific races become open to the whole
--         faction, so a Human Hunter can take the Dwarf or Night Elf chain.
-- Part B: Shaman and Paladin have no quest givers on the other faction.
--         Their class quests are opened to all races (helps where the giver
--         is neutral), and the stock neutral "World Shaman Trainer" (4991)
--         and "World Paladin Trainer" (4988) become trainer + vendor for the
--         rewards those quests would have given.
--
-- Safe to run on every startup: every statement is a no-op the second time.
--
-- Race masks: 77 = Human|Dwarf|Night Elf|Gnome (Alliance)
--             178 = Orc|Undead|Tauren|Troll (Horde)
--             255 = all eight races, 0 = no race check
-- =============================================================================

-- -----------------------------------------------------------------------------
-- B1. Shaman (class mask 64) and Paladin (class mask 2) quests: drop the race
--     check entirely. Runs before Part A so those rows are skipped there.
-- -----------------------------------------------------------------------------
UPDATE quest_template
SET RequiredRaces = 0
WHERE RequiredClasses IN (2, 64)
  AND RequiredRaces <> 0;

-- -----------------------------------------------------------------------------
-- A. Every other class-restricted quest: widen a race lock to its faction.
--    Quests already at 77, 178, 255 or 0 are untouched.
-- -----------------------------------------------------------------------------
UPDATE quest_template
SET RequiredRaces = 77
WHERE RequiredClasses <> 0
  AND RequiredRaces <> 0
  AND (RequiredRaces & 178) = 0
  AND RequiredRaces <> 77;

UPDATE quest_template
SET RequiredRaces = 178
WHERE RequiredClasses <> 0
  AND RequiredRaces <> 0
  AND (RequiredRaces & 77) = 0
  AND RequiredRaces <> 178;

-- -----------------------------------------------------------------------------
-- B2. Give the two neutral trainers the vendor flag (4). They keep gossip (1),
--     quest giver (2) and trainer (16): 19 -> 23. The default gossip menu
--     then offers both "Train me." and "I want to browse your goods."
-- -----------------------------------------------------------------------------
UPDATE creature_template
SET NpcFlags = NpcFlags | 4
WHERE Entry IN (4988, 4991);

-- -----------------------------------------------------------------------------
-- B3. Vendor stock: item rewards from the class quests.
--     maxcount 0 / incrtime 0 = unlimited stock. Price is the item's own
--     BuyPrice: totems are free, the gear costs gold.
-- -----------------------------------------------------------------------------
INSERT IGNORE INTO npc_vendor (entry, item, maxcount, incrtime, slot, condition_id, comments) VALUES
  -- World Shaman Trainer
  (4991, 5175,  0, 0, 0, 0, 'Earth Totem (Call of Earth, level 4)'),
  (4991, 5176,  0, 0, 1, 0, 'Fire Totem (Call of Fire, level 10)'),
  (4991, 5177,  0, 0, 2, 0, 'Water Totem (Call of Water, level 20)'),
  (4991, 5178,  0, 0, 3, 0, 'Air Totem (Call of Air, level 30)'),
  (4991, 20369, 0, 0, 4, 0, 'Azurite Fists (Da Voodoo, level 50)'),
  (4991, 20503, 0, 0, 5, 0, 'Enamored Water Spirit (Da Voodoo, level 50)'),
  (4991, 20556, 0, 0, 6, 0, 'Wildstaff (Da Voodoo, level 50)'),
  -- World Paladin Trainer
  (4988, 9607,  0, 0, 0, 0, 'Bastion of Stormwind (The Tome of Valor, level 20)'),
  (4988, 6953,  0, 0, 1, 0, 'Verigan''s Fist (The Test of Righteousness, level 20)'),
  (4988, 20504, 0, 0, 2, 0, 'Lightforged Blade (Forging the Mightstone, level 50)'),
  (4988, 20512, 0, 0, 3, 0, 'Sanctified Orb (Forging the Mightstone, level 50)'),
  (4988, 20505, 0, 0, 4, 0, 'Chivalrous Signet (Forging the Mightstone, level 50)'),
  (4988, 20620, 0, 0, 5, 0, 'Holy Mightstone (Forging the Mightstone, level 50)');

-- -----------------------------------------------------------------------------
-- B4. Spells the class quests teach. A vendor cannot sell a spell, so these
--     go on the same NPC's trainer list, free, at the quest's level.
--     Each id is the "learn" spell, matching the stock trainer tables.
--     Without the rank 1 totems a shaman cannot train the higher ranks.
-- -----------------------------------------------------------------------------
INSERT IGNORE INTO npc_trainer (entry, spell, spellcost, reqskill, reqskillvalue, reqlevel, condition_id) VALUES
  -- World Shaman Trainer
  (4991, 8073,  0, 0, 0, 4,  0),  -- teaches Stoneskin Totem (8071), from Call of Earth
  (4991, 2075,  0, 0, 0, 10, 0),  -- teaches Searing Totem (3599), from Call of Fire
  (4991, 5396,  0, 0, 0, 20, 0),  -- teaches Healing Stream Totem (5394), from Call of Water
  -- World Paladin Trainer
  (4988, 7329,  0, 0, 0, 12, 0),  -- teaches Redemption (7328), from The Tome of Divinity
  (4988, 5503,  0, 0, 0, 20, 0),  -- teaches Sense Undead (5502), from The Tome of Valor
  (4988, 13820, 0, 0, 0, 40, 0),  -- teaches Summon Warhorse (13819), from The Tome of Nobility
  (4988, 23215, 0, 0, 0, 60, 0);  -- teaches Summon Charger (23214), from Judgment and Redemption
