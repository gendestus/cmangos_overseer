-- =============================================================================
-- all_race_class_combos.sql
-- Enables every class for every race on CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/all_race_class_combos.sql
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- Stock data defines 40 of the 72 race/class pairs. This adds the other 32.
-- Safe to run on every startup: stock rows are never modified and new rows
-- are only inserted when missing (INSERT IGNORE, or NOT EXISTS for the one
-- table without a unique key). Nothing is updated or deleted.
--
-- Method: each new pair borrows its class data from a "donor" race that has
-- the class in stock data, and its racial data from its own race's Warrior
-- (every race has a stock Warrior).
--
-- Race ids:  1 Human  2 Orc  3 Dwarf  4 Night Elf  5 Undead  6 Tauren
--            7 Gnome  8 Troll
-- Class ids: 1 Warrior  2 Paladin  3 Hunter  4 Rogue  5 Priest  7 Shaman
--            8 Mage  9 Warlock  11 Druid
-- =============================================================================

DROP TEMPORARY TABLE IF EXISTS arc_new;
DROP TEMPORARY TABLE IF EXISTS arc_race_spell;
DROP TEMPORARY TABLE IF EXISTS arc_skip_spell;
DROP TEMPORARY TABLE IF EXISTS arc_race_action;
DROP TEMPORARY TABLE IF EXISTS arc_outfit;

-- -----------------------------------------------------------------------------
-- 1. The 32 new pairs and the donor race each one borrows class data from.
--    Donors per class: Paladin<-Human, Hunter<-Orc, Rogue<-Undead,
--    Priest<-Dwarf, Shaman<-Orc, Mage<-Troll, Warlock<-Undead, Druid<-Night Elf
--    (chosen as the stock race whose stat curve best predicts the others).
-- -----------------------------------------------------------------------------
CREATE TEMPORARY TABLE arc_new (
  race       TINYINT UNSIGNED NOT NULL,
  class      TINYINT UNSIGNED NOT NULL,
  donor_race TINYINT UNSIGNED NOT NULL,
  PRIMARY KEY (race, class)
);

INSERT INTO arc_new (race, class, donor_race) VALUES
  -- Paladin (stock: Human, Dwarf)
  (2, 2, 1), (4, 2, 1), (5, 2, 1), (6, 2, 1), (7, 2, 1), (8, 2, 1),
  -- Hunter (stock: Orc, Dwarf, Night Elf, Tauren, Troll)
  (1, 3, 2), (5, 3, 2), (7, 3, 2),
  -- Rogue (stock: all but Tauren)
  (6, 4, 5),
  -- Priest (stock: Human, Dwarf, Night Elf, Undead, Troll)
  (2, 5, 3), (6, 5, 3), (7, 5, 3),
  -- Shaman (stock: Orc, Tauren, Troll)
  (1, 7, 2), (3, 7, 2), (4, 7, 2), (5, 7, 2), (7, 7, 2),
  -- Mage (stock: Human, Undead, Gnome, Troll)
  (2, 8, 8), (3, 8, 8), (4, 8, 8), (6, 8, 8),
  -- Warlock (stock: Human, Orc, Undead, Gnome)
  (3, 9, 5), (4, 9, 5), (6, 9, 5), (8, 9, 5),
  -- Druid (stock: Night Elf, Tauren)
  (1, 11, 4), (2, 11, 4), (3, 11, 4), (5, 11, 4), (7, 11, 4), (8, 11, 4);

-- -----------------------------------------------------------------------------
-- 2. Level stats (60 rows per pair). MUST exist before the create-info row:
--    mangosd exits at startup if a creatable pair has no level 1 stats.
--    stat(race, class, level) = donor's curve for the class, shifted by the
--    difference between the two races' level 1 Warrior stats.
-- -----------------------------------------------------------------------------
INSERT IGNORE INTO player_levelstats (race, class, level, str, agi, sta, inte, spi)
SELECT
  n.race, n.class, d.level,
  LEAST(255, GREATEST(1, CAST(d.str  AS SIGNED) + CAST(rb.str  AS SIGNED) - CAST(db.str  AS SIGNED))),
  LEAST(255, GREATEST(1, CAST(d.agi  AS SIGNED) + CAST(rb.agi  AS SIGNED) - CAST(db.agi  AS SIGNED))),
  LEAST(255, GREATEST(1, CAST(d.sta  AS SIGNED) + CAST(rb.sta  AS SIGNED) - CAST(db.sta  AS SIGNED))),
  LEAST(255, GREATEST(1, CAST(d.inte AS SIGNED) + CAST(rb.inte AS SIGNED) - CAST(db.inte AS SIGNED))),
  LEAST(255, GREATEST(1, CAST(d.spi  AS SIGNED) + CAST(rb.spi  AS SIGNED) - CAST(db.spi  AS SIGNED)))
FROM arc_new n
JOIN player_levelstats d  ON d.race  = n.donor_race AND d.class  = n.class
JOIN player_levelstats rb ON rb.race = n.race       AND rb.class = 1 AND rb.level = 1
JOIN player_levelstats db ON db.race = n.donor_race AND db.class = 1 AND db.level = 1;

-- -----------------------------------------------------------------------------
-- 3. Starting skills. Class skill lines, armor and most weapons are already
--    mask-based (raceMask 0 = all races) and need nothing.
--    Hunter is the exception: SkillRaceClassInfo.dbc only admits axes, guns
--    and daggers for the five stock hunter races, so Human/Undead/Gnome
--    hunters (raceMask 1|16|64 = 81) start with bow + one-handed sword.
-- -----------------------------------------------------------------------------
INSERT IGNORE INTO playercreateinfo_skills (raceMask, classMask, skill, step, note) VALUES
  (81, 4, 45, 0, 'Weapon: Bows (Hunter, added races)'),
  (81, 4, 43, 0, 'Weapon: Swords (Hunter, added races)');

-- -----------------------------------------------------------------------------
-- 4. Starting spells = donor's class spells + own race's racials.
-- -----------------------------------------------------------------------------
-- Every racial spell in stock data. grant_new = 1: given to new pairs of that
-- race. grant_new = 0: class-specific variant, listed only so it is stripped
-- from donor rows (new Orc/Troll pairs get the default variant instead).
CREATE TEMPORARY TABLE arc_race_spell (
  race      TINYINT UNSIGNED   NOT NULL,
  spell     MEDIUMINT UNSIGNED NOT NULL,
  note      VARCHAR(255)       NOT NULL,
  grant_new TINYINT UNSIGNED   NOT NULL DEFAULT 1,
  PRIMARY KEY (race, spell)
);

INSERT INTO arc_race_spell (race, spell, note, grant_new) VALUES
  (1, 668,   'Language Common', 1),
  (1, 20597, 'Sword Specialization', 1),
  (1, 20598, 'The Human Spirit', 1),
  (1, 20599, 'Diplomacy', 1),
  (1, 20600, 'Perception', 1),
  (1, 20864, 'Mace Specialization', 1),
  (2, 669,   'Language Orcish', 1),
  (2, 20572, 'Blood Fury', 1),
  (2, 20573, 'Hardiness', 1),
  (2, 20574, 'Axe Specialization', 1),
  (2, 21563, 'Command', 1),            -- default variant (pet melee damage)
  (2, 20575, 'Command', 0),            -- Warlock variant
  (2, 20576, 'Command', 0),            -- Hunter variant
  (3, 668,   'Language Common', 1),
  (3, 672,   'Language Dwarven', 1),
  (3, 2481,  'Find Treasure', 1),
  (3, 20594, 'Stoneform', 1),
  (3, 20595, 'Gun Specialization', 1),
  (3, 20596, 'Frost Resistance', 1),
  (4, 668,   'Language Common', 1),
  (4, 671,   'Language Darnassian', 1),
  (4, 20580, 'Shadowmeld', 1),
  (4, 20582, 'Quickness', 1),
  (4, 20583, 'Nature Resistance', 1),
  (4, 20585, 'Wisp Spirit', 1),
  (4, 21009, 'Shadowmeld Passive', 1),
  (5, 669,   'Language Orcish', 1),
  (5, 5227,  'Underwater Breathing', 1),
  (5, 7744,  'Will of the Forsaken', 1),
  (5, 17737, 'Language Gutterspeak', 1),
  (5, 20577, 'Cannibalize', 1),
  (5, 20579, 'Shadow Resistance', 1),
  (6, 669,   'Language Orcish', 1),
  (6, 670,   'Language Taurahe', 1),
  (6, 20549, 'War Stomp', 1),
  (6, 20550, 'Endurance', 1),
  (6, 20551, 'Nature Resistance', 1),
  (6, 20552, 'Cultivation', 1),
  (7, 668,   'Language Common', 1),
  (7, 7340,  'Language Gnomish', 1),
  (7, 20589, 'Escape Artist', 1),
  (7, 20591, 'Expansive Mind', 1),
  (7, 20592, 'Arcane Resistance', 1),
  (7, 20593, 'Engineering Specialization', 1),
  (8, 669,   'Language Orcish', 1),
  (8, 7341,  'Language Troll', 1),
  (8, 20555, 'Regeneration', 1),
  (8, 20557, 'Beast Slaying', 1),
  (8, 20558, 'Throwing Specialization', 1),
  (8, 26290, 'Bow Specialization', 1),
  (8, 20554, 'Berserking', 1),         -- mana variant (all new Troll pairs use mana)
  (8, 26296, 'Berserking', 0),         -- rage variant
  (8, 26297, 'Berserking', 0);         -- energy variant

-- Weapon spells that belong to the donor's race/class pair, not the class.
CREATE TEMPORARY TABLE arc_skip_spell (
  class TINYINT UNSIGNED   NOT NULL,
  spell MEDIUMINT UNSIGNED NOT NULL,
  PRIMARY KEY (class, spell)
);

INSERT INTO arc_skip_spell (class, spell) VALUES
  (3, 196),    -- Orc Hunter: One-Handed Axes (DBC-locked for the added hunter races)
  (11, 1180);  -- Night Elf Druid: Daggers (DBC-locked to Night Elf)

-- 4a. Class part: donor's spells minus anything racial or pair-specific.
INSERT IGNORE INTO playercreateinfo_spell (race, class, Spell, Note)
SELECT n.race, n.class, s.Spell, s.Note
FROM arc_new n
JOIN playercreateinfo_spell s ON s.race = n.donor_race AND s.class = n.class
WHERE s.Spell NOT IN (SELECT spell FROM arc_race_spell)
  AND NOT EXISTS (SELECT 1 FROM arc_skip_spell k WHERE k.class = n.class AND k.spell = s.Spell);

-- 4b. Racial part.
INSERT IGNORE INTO playercreateinfo_spell (race, class, Spell, Note)
SELECT n.race, n.class, x.spell, x.note
FROM arc_new n
JOIN arc_race_spell x ON x.race = n.race
WHERE x.grant_new = 1;

-- 4c. Added hunter races: sword proficiency (bows come from the Orc donor).
INSERT IGNORE INTO playercreateinfo_spell (race, class, Spell, Note) VALUES
  (1, 3, 201, 'One-Handed Swords'),
  (5, 3, 201, 'One-Handed Swords'),
  (7, 3, 201, 'One-Handed Swords');

-- -----------------------------------------------------------------------------
-- 5. Starting gear. Stock gear comes from CharStartOutfit.dbc, which has no
--    row for the new pairs, so they would spawn with nothing. One outfit per
--    class, copied from a stock race's DBC outfit. bar_button puts the
--    consumable on the action bar (step 6).
--    Dwarf Mage is skipped: the DBC already ships an outfit for it.
-- -----------------------------------------------------------------------------
CREATE TEMPORARY TABLE arc_outfit (
  class      TINYINT UNSIGNED   NOT NULL,
  ord        TINYINT UNSIGNED   NOT NULL,
  itemid     MEDIUMINT UNSIGNED NOT NULL,
  amount     TINYINT UNSIGNED   NOT NULL,
  bar_button TINYINT UNSIGNED   NULL,
  PRIMARY KEY (class, ord)
);

INSERT INTO arc_outfit (class, ord, itemid, amount, bar_button) VALUES
  -- Paladin (Human outfit)
  (2, 1, 2361, 1, NULL),   -- Battleworn Hammer
  (2, 2, 45,   1, NULL),   -- Squire's Shirt
  (2, 3, 44,   1, NULL),   -- Squire's Pants
  (2, 4, 43,   1, NULL),   -- Squire's Boots
  (2, 5, 159,  2, 10),     -- Refreshing Spring Water
  (2, 6, 2070, 4, 11),     -- Darnassian Bleu
  (2, 7, 6948, 1, NULL),   -- Hearthstone
  -- Hunter (Orc outfit, Worn Axe swapped for Worn Shortsword)
  (3, 1, 25,   1, NULL),   -- Worn Shortsword
  (3, 2, 2504, 1, NULL),   -- Worn Shortbow
  (3, 3, 2101, 1, NULL),   -- Light Quiver
  (3, 4, 2512, 200, NULL), -- Rough Arrow
  (3, 5, 127,  1, NULL),   -- Trapper's Shirt
  (3, 6, 6126, 1, NULL),   -- Trapper's Pants
  (3, 7, 6127, 1, NULL),   -- Trapper's Boots
  (3, 8, 159,  2, 10),     -- Refreshing Spring Water
  (3, 9, 117,  4, 11),     -- Tough Jerky
  (3, 10, 6948, 1, NULL),  -- Hearthstone
  -- Rogue (Human outfit)
  (4, 1, 2092, 1, NULL),   -- Worn Dagger
  (4, 2, 2947, 200, NULL), -- Small Throwing Knife
  (4, 3, 49,   1, NULL),   -- Footpad's Shirt
  (4, 4, 48,   1, NULL),   -- Footpad's Pants
  (4, 5, 47,   1, NULL),   -- Footpad's Shoes
  (4, 6, 2070, 4, 11),     -- Darnassian Bleu
  (4, 7, 6948, 1, NULL),   -- Hearthstone
  -- Priest (Human outfit)
  (5, 1, 36,   1, NULL),   -- Worn Mace
  (5, 2, 6098, 1, NULL),   -- Neophyte's Robe
  (5, 3, 53,   1, NULL),   -- Neophyte's Shirt
  (5, 4, 52,   1, NULL),   -- Neophyte's Pants
  (5, 5, 51,   1, NULL),   -- Neophyte's Boots
  (5, 6, 159,  2, 10),     -- Refreshing Spring Water
  (5, 7, 2070, 4, 11),     -- Darnassian Bleu
  (5, 8, 6948, 1, NULL),   -- Hearthstone
  -- Shaman (Orc outfit)
  (7, 1, 36,   1, NULL),   -- Worn Mace
  (7, 2, 154,  1, NULL),   -- Primitive Mantle
  (7, 3, 153,  1, NULL),   -- Primitive Kilt
  (7, 4, 159,  2, 10),     -- Refreshing Spring Water
  (7, 5, 117,  4, 11),     -- Tough Jerky
  (7, 6, 6948, 1, NULL),   -- Hearthstone
  -- Mage (Human outfit)
  (8, 1, 35,   1, NULL),   -- Bent Staff
  (8, 2, 56,   1, NULL),   -- Apprentice's Robe
  (8, 3, 6096, 1, NULL),   -- Apprentice's Shirt
  (8, 4, 1395, 1, NULL),   -- Apprentice's Pants
  (8, 5, 55,   1, NULL),   -- Apprentice's Boots
  (8, 6, 159,  2, 10),     -- Refreshing Spring Water
  (8, 7, 2070, 4, 11),     -- Darnassian Bleu
  (8, 8, 6948, 1, NULL),   -- Hearthstone
  -- Warlock (Human outfit)
  (9, 1, 2092, 1, NULL),   -- Worn Dagger
  (9, 2, 57,   1, NULL),   -- Acolyte's Robe
  (9, 3, 6097, 1, NULL),   -- Acolyte's Shirt
  (9, 4, 1396, 1, NULL),   -- Acolyte's Pants
  (9, 5, 59,   1, NULL),   -- Acolyte's Shoes
  (9, 6, 159,  2, 10),     -- Refreshing Spring Water
  (9, 7, 4604, 4, 11),     -- Forest Mushroom Cap
  (9, 8, 6948, 1, NULL),   -- Hearthstone
  -- Druid (Night Elf outfit)
  (11, 1, 3661, 1, NULL),  -- Handcrafted Staff
  (11, 2, 6123, 1, NULL),  -- Novice's Robe
  (11, 3, 6124, 1, NULL),  -- Novice's Pants
  (11, 4, 159,  2, 10),    -- Refreshing Spring Water
  (11, 5, 4536, 4, 11),    -- Shiny Red Apple
  (11, 6, 6948, 1, NULL);  -- Hearthstone

-- playercreateinfo_item has no unique key, so INSERT IGNORE would duplicate
-- rows on every start. Insert only what is missing instead.
INSERT INTO playercreateinfo_item (race, class, itemid, amount)
SELECT n.race, n.class, o.itemid, o.amount
FROM arc_new n
JOIN arc_outfit o ON o.class = n.class
WHERE NOT (n.race = 3 AND n.class = 8)
  AND NOT EXISTS (
    SELECT 1 FROM playercreateinfo_item i
    WHERE i.race = n.race AND i.class = n.class AND i.itemid = o.itemid
  )
ORDER BY n.race, n.class, o.ord;

-- -----------------------------------------------------------------------------
-- 6. Action bar, following the stock layout:
--    buttons 0-2 class abilities (0-3 for Rogue), then racial actives,
--    button 10 water, button 11 food.
-- -----------------------------------------------------------------------------
-- 6a. Class abilities from the donor.
INSERT IGNORE INTO playercreateinfo_action (race, class, button, action, type)
SELECT n.race, n.class, a.button, a.action, a.type
FROM arc_new n
JOIN playercreateinfo_action a ON a.race = n.donor_race AND a.class = n.class
WHERE a.button <= IF(n.class = 4, 3, 2);

-- 6b. Racial actives (the same ones stock data puts on the bar).
CREATE TEMPORARY TABLE arc_race_action (
  race  TINYINT UNSIGNED   NOT NULL,
  ord   TINYINT UNSIGNED   NOT NULL,
  spell MEDIUMINT UNSIGNED NOT NULL,
  PRIMARY KEY (race, ord)
);

INSERT INTO arc_race_action (race, ord, spell) VALUES
  (2, 0, 20572),  -- Orc: Blood Fury
  (3, 0, 20594),  -- Dwarf: Stoneform
  (3, 1, 2481),   -- Dwarf: Find Treasure
  (4, 0, 20580),  -- Night Elf: Shadowmeld
  (5, 0, 20577),  -- Undead: Cannibalize
  (6, 0, 20549);  -- Tauren: War Stomp

INSERT IGNORE INTO playercreateinfo_action (race, class, button, action, type)
SELECT n.race, n.class, IF(n.class = 4, 4, 3) + r.ord, r.spell, 0
FROM arc_new n
JOIN arc_race_action r ON r.race = n.race;

-- 6c. Water and food (type 128 = item).
INSERT IGNORE INTO playercreateinfo_action (race, class, button, action, type)
SELECT n.race, n.class, o.bar_button, o.itemid, 128
FROM arc_new n
JOIN arc_outfit o ON o.class = n.class
WHERE o.bar_button IS NOT NULL
  AND NOT (n.race = 3 AND n.class = 8);

-- Dwarf Mage uses its DBC outfit: water 159, Tough Hunk of Bread 4540.
INSERT IGNORE INTO playercreateinfo_action (race, class, button, action, type) VALUES
  (3, 8, 10, 159, 128),
  (3, 8, 11, 4540, 128);

-- -----------------------------------------------------------------------------
-- 7. LAST: the create-info rows that make the pairs creatable. Start position
--    is the race's stock start (copied from its Warrior row).
-- -----------------------------------------------------------------------------
INSERT IGNORE INTO playercreateinfo (race, class, map, zone, position_x, position_y, position_z, orientation)
SELECT n.race, n.class, w.map, w.zone, w.position_x, w.position_y, w.position_z, w.orientation
FROM arc_new n
JOIN playercreateinfo w ON w.race = n.race AND w.class = 1;

DROP TEMPORARY TABLE IF EXISTS arc_new;
DROP TEMPORARY TABLE IF EXISTS arc_race_spell;
DROP TEMPORARY TABLE IF EXISTS arc_skip_spell;
DROP TEMPORARY TABLE IF EXISTS arc_race_action;
DROP TEMPORARY TABLE IF EXISTS arc_outfit;
