-- =============================================================================
-- spell_books.sql
-- Cross-class spell books for CMaNGOS Classic (1.12.1): eight Stormwind book
-- vendors, 750 vendor books and 68 capstone books, generated from the reviewed
-- spell plan (spell_book_plan.xlsx).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/spell_books.sql
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- GENERATED FILE. To change tiers, names or prices, edit the plan workbook
-- and regenerate; hand edits here are lost on the next generation.
--
-- What it creates
--   * Vendors (neutral, level 40, vendor only). Spawn with .npc add <id>:
--       90010  Tessa Quillwright  <Arcane Tomes>  125 books  (Mage)
--       90011  Malrick Vane       <Fel Grimoires>  117 books  (Warlock)
--       90012  Sister Adelyn      <Holy Codices>  115 books  (Priest)
--       90013  Sir Corwin Hale    <Sacred Librams>  102 books  (Paladin)
--       90014  Lirael Mossglade   <Books of the Wild>   83 books  (Druid (caster))
--       90015  Borgan Stormhand   <Elemental Tablets>   76 books  (Shaman (spells))
--       90016  Hilda Stormhand    <Totem Tablets>   62 books  (Shaman (totems))
--       90017  Garrick Thornpaw   <Books of the Claw>   70 books  (Druid (feral))
--   * Vendor books: one per trainer rank. Price and level are the stock
--     trainer cost and level for that rank. White quality.
--   * Capstone books: one per spell, teaching the top trainer rank. Blue
--     quality, no price. NOT sold and NOT in any loot table yet: hand them out
--     with .additem or .send items until drop sources are decided.
--   * Every book is usable by the six caster classes only (class mask 1490)
--     and the vendors list them for those classes only.
--   * Druid form-ability books require knowing the form (Bear Form 5487 or
--     Cat Form 768). The Dire Bear Form book requires Bear Form.
--
-- How a book works: a consumable whose on-use spell is the stock "teach"
-- spell the class trainer uses. Learning a rank also grants the lower ranks
-- of that spell where the server chains them.
--
-- Ids: item id = 100000 + teach spell id, so ids never move between
-- generations and a book in someone's bag stays the same book.
--
-- Also changes one stock thing: the four totem relics on the World Shaman
-- Trainer (4991) become shaman-only, because the plan makes relics a capstone
-- for other classes.
--
-- Safe to run on every startup: custom rows are rewritten in place and the
-- eight vendors' stock is rebuilt from this file each time.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Vendor filter: player's class is in mask 1490 (Paladin, Priest, Shaman,
--    Mage, Warlock, Druid). The id must be below 65536 because the server
--    reads a vendor row's condition id as a 16-bit number. Shared with
--    spellbook_vendor.sql: whichever runs first creates it.
-- -----------------------------------------------------------------------------
DELETE FROM conditions WHERE condition_entry = 900001;

SET @sbk_cond := (
  SELECT condition_entry FROM conditions
  WHERE type = 14 AND value1 = 0 AND value2 = 1490 AND value3 = 0 AND value4 = 0 AND flags = 0
    AND condition_entry < 65536
  LIMIT 1
);

SET @sbk_cond := IFNULL(@sbk_cond, (
  SELECT IFNULL(MAX(condition_entry) + 1, 60001) FROM conditions
  WHERE condition_entry BETWEEN 60001 AND 65534
));

INSERT IGNORE INTO conditions (condition_entry, type, value1, value2, value3, value4, flags, comments) VALUES
  (@sbk_cond, 14, 0, 1490, 0, 0, 0, 'Player ClassMask: 1490 (spell book casters)');

-- -----------------------------------------------------------------------------
-- 2. Vendors. Each is a clone of Cowardly Crosby (2672), a stock neutral
--    level 40 vendor, with its own name and a borrowed stock appearance.
--    (The appearance column is DisplayId1 on a current database; it was
--    ModelId1 in the raw world dump before the content updates.)
--    Appearances come from stock trainers that stand outside Stormwind, so a
--    vendor never matches the NPC beside it:
--      90010 Jennea Cannon (human female)        90011 Maximillian Crowe (human male)
--      90012 Priestess Josetta (human female)    90013 Brother Wilhelm (human male)
--      90014 Laurna Morninglight (night elf f.)  90017 Mathrengyl Bearwalker (night elf m.)
--      90015 Grif Wildheart (dwarf male)         90016 Daera Brightspear (dwarf female)
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS sbk_npc_def;
CREATE TEMPORARY TABLE sbk_npc_def (
  entry   MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  name    VARCHAR(100) NOT NULL,
  subname VARCHAR(100) NOT NULL,
  model   MEDIUMINT UNSIGNED NOT NULL
);

INSERT INTO sbk_npc_def (entry, name, subname, model) VALUES
  (90010, 'Tessa Quillwright', 'Arcane Tomes', 3292),
  (90011, 'Malrick Vane', 'Fel Grimoires', 3271),
  (90012, 'Sister Adelyn', 'Holy Codices', 1295),
  (90013, 'Sir Corwin Hale', 'Sacred Librams', 1299),
  (90014, 'Lirael Mossglade', 'Books of the Wild', 1708),
  (90015, 'Borgan Stormhand', 'Elemental Tablets', 3558),
  (90016, 'Hilda Stormhand', 'Totem Tablets', 3056),
  (90017, 'Garrick Thornpaw', 'Books of the Claw', 2261);

DROP TEMPORARY TABLE IF EXISTS sbk_npc;
CREATE TEMPORARY TABLE sbk_npc AS
SELECT t.*, d.entry AS sbk_new_entry
FROM creature_template t
CROSS JOIN sbk_npc_def d
WHERE t.Entry = 2672;

UPDATE sbk_npc s
JOIN sbk_npc_def d ON d.entry = s.sbk_new_entry
SET s.Entry    = d.entry,
    s.Name     = d.name,
    s.SubName  = d.subname,
    s.DisplayId1 = d.model;

ALTER TABLE sbk_npc DROP COLUMN sbk_new_entry;

REPLACE INTO creature_template SELECT * FROM sbk_npc;

DROP TEMPORARY TABLE IF EXISTS sbk_npc;
DROP TEMPORARY TABLE IF EXISTS sbk_npc_def;

-- -----------------------------------------------------------------------------
-- 3. Book definitions. One row per book.
--    quality 1 = vendor book, 3 = capstone. vendor NULL = not sold.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS sbk_def;
CREATE TEMPORARY TABLE sbk_def (
  entry          MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  displayid      MEDIUMINT UNSIGNED NOT NULL,
  teach_spell    MEDIUMINT UNSIGNED NOT NULL,
  req_level      TINYINT UNSIGNED NOT NULL,
  buy_price      INT UNSIGNED NOT NULL,
  sell_price     INT UNSIGNED NOT NULL,
  quality        TINYINT UNSIGNED NOT NULL,
  required_spell MEDIUMINT UNSIGNED NOT NULL,
  vendor         MEDIUMINT UNSIGNED NULL,
  slot           TINYINT UNSIGNED NULL
);

-- Mage: 125 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (101472, 'Tome of Arcane Intellect I', 1103, 1472, 1, 10, 2, 1, 0, 90010, 0),
  (105507, 'Tome of Conjure Water I', 1103, 5507, 4, 100, 25, 1, 0, 90010, 1),
  (101142, 'Tome of Frostbolt I', 1103, 1142, 4, 100, 25, 1, 0, 90010, 2),
  (101249, 'Tome of Conjure Food I', 1103, 1249, 6, 100, 25, 1, 0, 90010, 3),
  (102141, 'Tome of Fire Blast I', 1103, 2141, 6, 100, 25, 1, 0, 90010, 4),
  (101173, 'Tome of Fireball II', 1103, 1173, 6, 100, 25, 1, 0, 90010, 5),
  (105146, 'Tome of Arcane Missiles I', 1103, 5146, 8, 200, 50, 1, 0, 90010, 6),
  (101191, 'Tome of Frostbolt II', 1103, 1191, 8, 200, 50, 1, 0, 90010, 7),
  (105565, 'Tome of Conjure Water II', 1103, 5565, 10, 400, 100, 1, 0, 90010, 8),
  (101174, 'Tome of Frost Armor II', 1103, 1174, 10, 400, 100, 1, 0, 90010, 9),
  (101194, 'Tome of Frost Nova I', 1103, 1194, 10, 400, 100, 1, 0, 90010, 10),
  (101250, 'Tome of Conjure Food II', 1103, 1250, 12, 600, 150, 1, 0, 90010, 11),
  (101266, 'Tome of Dampen Magic I', 1103, 1266, 12, 600, 150, 1, 0, 90010, 12),
  (101198, 'Tome of Fireball III', 1103, 1198, 12, 600, 150, 1, 0, 90010, 13),
  (106493, 'Tome of Slow Fall', 1103, 6493, 12, 600, 150, 1, 0, 90010, 14),
  (101467, 'Tome of Arcane Explosion I', 1103, 1467, 14, 900, 225, 1, 0, 90010, 15),
  (101473, 'Tome of Arcane Intellect II', 1103, 1473, 14, 900, 225, 1, 0, 90010, 16),
  (102142, 'Tome of Fire Blast II', 1103, 2142, 14, 900, 225, 1, 0, 90010, 17),
  (101211, 'Tome of Frostbolt III', 1103, 1211, 14, 900, 225, 1, 0, 90010, 18),
  (105147, 'Tome of Arcane Missiles II', 1103, 5147, 16, 1500, 375, 1, 0, 90010, 19),
  (102858, 'Tome of Detect Magic', 1103, 2858, 16, 1500, 375, 1, 0, 90010, 20),
  (102124, 'Tome of Flamestrike I', 1103, 2124, 16, 1500, 375, 1, 0, 90010, 21),
  (101267, 'Tome of Amplify Magic I', 1103, 1267, 18, 1800, 450, 1, 0, 90010, 22),
  (103142, 'Tome of Fireball IV', 1103, 3142, 18, 1800, 450, 1, 0, 90010, 23),
  (101176, 'Tome of Remove Lesser Curse', 1103, 1176, 18, 1800, 450, 1, 0, 90010, 24),
  (101196, 'Tome of Blizzard I', 1103, 1196, 20, 2000, 500, 1, 0, 90010, 25),
  (105566, 'Tome of Conjure Water III', 1103, 5566, 20, 2000, 500, 1, 0, 90010, 26),
  (101035, 'Tome of Fire Ward I', 1103, 1035, 20, 2000, 500, 1, 0, 90010, 27),
  (101200, 'Tome of Frost Armor III', 1103, 1200, 20, 2000, 500, 1, 0, 90010, 28),
  (107323, 'Tome of Frostbolt IV', 1103, 7323, 20, 2000, 500, 1, 0, 90010, 29),
  (101481, 'Tome of Mana Shield I', 1103, 1481, 20, 2000, 500, 1, 0, 90010, 30),
  (108440, 'Tome of Arcane Explosion II', 1103, 8440, 22, 3000, 750, 1, 0, 90010, 31),
  (101251, 'Tome of Conjure Food III', 1103, 1251, 22, 3000, 750, 1, 0, 90010, 32),
  (102143, 'Tome of Fire Blast III', 1103, 2143, 22, 3000, 750, 1, 0, 90010, 33),
  (106144, 'Tome of Frost Ward I', 1103, 6144, 22, 3000, 750, 1, 0, 90010, 34),
  (101811, 'Tome of Scorch I', 1103, 1811, 22, 3000, 750, 1, 0, 90010, 35),
  (105148, 'Tome of Arcane Missiles III', 1103, 5148, 24, 4000, 1000, 1, 0, 90010, 36),
  (108452, 'Tome of Dampen Magic II', 1103, 8452, 24, 4000, 1000, 1, 0, 90010, 37),
  (108403, 'Tome of Fireball V', 1103, 8403, 24, 4000, 1000, 1, 0, 90010, 38),
  (102125, 'Tome of Flamestrike II', 1103, 2125, 24, 4000, 1000, 1, 0, 90010, 39),
  (101241, 'Tome of Cone of Cold I', 1103, 1241, 26, 5000, 1250, 1, 0, 90010, 40),
  (101225, 'Tome of Frost Nova II', 1103, 1225, 26, 5000, 1250, 1, 0, 90010, 41),
  (108409, 'Tome of Frostbolt V', 1103, 8409, 26, 5000, 1250, 1, 0, 90010, 42),
  (101474, 'Tome of Arcane Intellect III', 1103, 1474, 28, 7000, 1750, 1, 0, 90010, 43),
  (106142, 'Tome of Blizzard II', 1103, 6142, 28, 7000, 1750, 1, 0, 90010, 44),
  (101210, 'Tome of Conjure Mana Agate', 1103, 1210, 28, 7000, 1750, 1, 0, 90010, 45),
  (108496, 'Tome of Mana Shield II', 1103, 8496, 28, 7000, 1750, 1, 0, 90010, 46),
  (108447, 'Tome of Scorch II', 1103, 8447, 28, 7000, 1750, 1, 0, 90010, 47),
  (108456, 'Tome of Amplify Magic II', 1103, 8456, 30, 8000, 2000, 1, 0, 90010, 48),
  (108441, 'Tome of Arcane Explosion III', 1103, 8441, 30, 8000, 2000, 1, 0, 90010, 49),
  (106128, 'Tome of Conjure Water IV', 1103, 6128, 30, 8000, 2000, 1, 0, 90010, 50),
  (108414, 'Tome of Fire Blast IV', 1103, 8414, 30, 8000, 2000, 1, 0, 90010, 51),
  (108459, 'Tome of Fire Ward II', 1103, 8459, 30, 8000, 2000, 1, 0, 90010, 52),
  (108404, 'Tome of Fireball VI', 1103, 8404, 30, 8000, 2000, 1, 0, 90010, 53),
  (101214, 'Tome of Ice Armor I', 1103, 1214, 30, 8000, 2000, 1, 0, 90010, 54),
  (108420, 'Tome of Arcane Missiles IV', 1103, 8420, 32, 10000, 2500, 1, 0, 90010, 55),
  (106130, 'Tome of Conjure Food IV', 1103, 6130, 32, 10000, 2500, 1, 0, 90010, 56),
  (108425, 'Tome of Flamestrike III', 1103, 8425, 32, 10000, 2500, 1, 0, 90010, 57),
  (108463, 'Tome of Frost Ward II', 1103, 8463, 32, 10000, 2500, 1, 0, 90010, 58),
  (108410, 'Tome of Frostbolt VI', 1103, 8410, 32, 10000, 2500, 1, 0, 90010, 59),
  (108493, 'Tome of Cone of Cold II', 1103, 8493, 34, 12000, 3000, 1, 0, 90010, 60),
  (106121, 'Tome of Mage Armor I', 1103, 6121, 34, 13000, 3250, 1, 0, 90010, 61),
  (108448, 'Tome of Scorch III', 1103, 8448, 34, 12000, 3000, 1, 0, 90010, 62),
  (108428, 'Tome of Blizzard III', 1103, 8428, 36, 13000, 3250, 1, 0, 90010, 63),
  (108453, 'Tome of Dampen Magic III', 1103, 8453, 36, 15000, 3750, 1, 0, 90010, 64),
  (108405, 'Tome of Fireball VII', 1103, 8405, 36, 13000, 3250, 1, 0, 90010, 65),
  (108497, 'Tome of Mana Shield III', 1103, 8497, 36, 13000, 3250, 1, 0, 90010, 66),
  (108442, 'Tome of Arcane Explosion IV', 1103, 8442, 38, 14000, 3500, 1, 0, 90010, 67),
  (103553, 'Tome of Conjure Mana Jade', 1103, 3553, 38, 14000, 3500, 1, 0, 90010, 68),
  (108415, 'Tome of Fire Blast V', 1103, 8415, 38, 14000, 3500, 1, 0, 90010, 69),
  (108411, 'Tome of Frostbolt VII', 1103, 8411, 38, 14000, 3500, 1, 0, 90010, 70),
  (108421, 'Tome of Arcane Missiles V', 1103, 8421, 40, 15000, 3750, 1, 0, 90010, 71),
  (110141, 'Tome of Conjure Water V', 1103, 10141, 40, 15000, 3750, 1, 0, 90010, 72),
  (108460, 'Tome of Fire Ward III', 1103, 8460, 40, 15000, 3750, 1, 0, 90010, 73),
  (108426, 'Tome of Flamestrike IV', 1103, 8426, 40, 15000, 3750, 1, 0, 90010, 74),
  (106132, 'Tome of Frost Nova III', 1103, 6132, 40, 15000, 3750, 1, 0, 90010, 75),
  (101228, 'Tome of Ice Armor II', 1103, 1228, 40, 15000, 3750, 1, 0, 90010, 76),
  (108449, 'Tome of Scorch IV', 1103, 8449, 40, 15000, 3750, 1, 0, 90010, 77),
  (110171, 'Tome of Amplify Magic III', 1103, 10171, 42, 18000, 4500, 1, 0, 90010, 78),
  (101475, 'Tome of Arcane Intellect IV', 1103, 1475, 42, 22750, 5687, 1, 0, 90010, 79),
  (110162, 'Tome of Cone of Cold III', 1103, 10162, 42, 22750, 5687, 1, 0, 90010, 80),
  (110146, 'Tome of Conjure Food V', 1103, 10146, 42, 22750, 5687, 1, 0, 90010, 81),
  (110152, 'Tome of Fireball VIII', 1103, 10152, 42, 22750, 5687, 1, 0, 90010, 82),
  (108464, 'Tome of Frost Ward III', 1103, 8464, 42, 18000, 4500, 1, 0, 90010, 83),
  (110188, 'Tome of Blizzard IV', 1103, 10188, 44, 23000, 5750, 1, 0, 90010, 84),
  (110182, 'Tome of Frostbolt VIII', 1103, 10182, 44, 23000, 5750, 1, 0, 90010, 85),
  (110194, 'Tome of Mana Shield IV', 1103, 10194, 44, 23000, 5750, 1, 0, 90010, 86),
  (110203, 'Tome of Arcane Explosion V', 1103, 10203, 46, 26000, 6500, 1, 0, 90010, 87),
  (110198, 'Tome of Fire Blast VI', 1103, 10198, 46, 26000, 6500, 1, 0, 90010, 88),
  (122784, 'Tome of Mage Armor II', 1103, 22784, 46, 28000, 7000, 1, 0, 90010, 89),
  (110208, 'Tome of Scorch V', 1103, 10208, 46, 26000, 6500, 1, 0, 90010, 90),
  (110213, 'Tome of Arcane Missiles VI', 1103, 10213, 48, 28000, 7000, 1, 0, 90010, 91),
  (110055, 'Tome of Conjure Mana Citrine', 1103, 10055, 48, 28000, 7000, 1, 0, 90010, 92),
  (110175, 'Tome of Dampen Magic IV', 1103, 10175, 48, 28000, 7000, 1, 0, 90010, 93),
  (110153, 'Tome of Fireball IX', 1103, 10153, 48, 28000, 7000, 1, 0, 90010, 94),
  (110217, 'Tome of Flamestrike V', 1103, 10217, 48, 28000, 7000, 1, 0, 90010, 95),
  (110163, 'Tome of Cone of Cold IV', 1103, 10163, 50, 32000, 8000, 1, 0, 90010, 96),
  (110142, 'Tome of Conjure Water VI', 1103, 10142, 50, 32000, 8000, 1, 0, 90010, 97),
  (110224, 'Tome of Fire Ward IV', 1103, 10224, 50, 32000, 8000, 1, 0, 90010, 98),
  (110183, 'Tome of Frostbolt IX', 1103, 10183, 50, 32000, 8000, 1, 0, 90010, 99),
  (110221, 'Tome of Ice Armor III', 1103, 10221, 50, 32000, 8000, 1, 0, 90010, 100),
  (110189, 'Tome of Blizzard V', 1103, 10189, 52, 35000, 8750, 1, 0, 90010, 101),
  (110147, 'Tome of Conjure Food VI', 1103, 10147, 52, 35000, 8750, 1, 0, 90010, 102),
  (110178, 'Tome of Frost Ward IV', 1103, 10178, 52, 35000, 8750, 1, 0, 90010, 103),
  (110195, 'Tome of Mana Shield V', 1103, 10195, 52, 35000, 8750, 1, 0, 90010, 104),
  (110209, 'Tome of Scorch VI', 1103, 10209, 52, 35000, 8750, 1, 0, 90010, 105),
  (110172, 'Tome of Amplify Magic IV', 1103, 10172, 54, 36000, 9000, 1, 0, 90010, 106),
  (110204, 'Tome of Arcane Explosion VI', 1103, 10204, 54, 36000, 9000, 1, 0, 90010, 107),
  (110200, 'Tome of Fire Blast VII', 1103, 10200, 54, 36000, 9000, 1, 0, 90010, 108),
  (110154, 'Tome of Fireball X', 1103, 10154, 54, 36000, 9000, 1, 0, 90010, 109),
  (110231, 'Tome of Frost Nova IV', 1103, 10231, 54, 36000, 9000, 1, 0, 90010, 110),
  (110158, 'Tome of Arcane Intellect V', 1103, 10158, 56, 38000, 9500, 1, 0, 90010, 111),
  (110214, 'Tome of Arcane Missiles VII', 1103, 10214, 56, 38000, 9500, 1, 0, 90010, 112),
  (110218, 'Tome of Flamestrike VI', 1103, 10218, 56, 38000, 9500, 1, 0, 90010, 113),
  (110184, 'Tome of Frostbolt X', 1103, 10184, 56, 38000, 9500, 1, 0, 90010, 114),
  (110164, 'Tome of Cone of Cold V', 1103, 10164, 58, 40000, 10000, 1, 0, 90010, 115),
  (110056, 'Tome of Conjure Mana Ruby', 1103, 10056, 58, 40000, 10000, 1, 0, 90010, 116),
  (122785, 'Tome of Mage Armor III', 1103, 22785, 58, 40000, 10000, 1, 0, 90010, 117),
  (110210, 'Tome of Scorch VII', 1103, 10210, 58, 40000, 10000, 1, 0, 90010, 118),
  (110190, 'Tome of Blizzard VI', 1103, 10190, 60, 42000, 10500, 1, 0, 90010, 119),
  (110176, 'Tome of Dampen Magic V', 1103, 10176, 60, 42000, 10500, 1, 0, 90010, 120),
  (110226, 'Tome of Fire Ward V', 1103, 10226, 60, 42000, 10500, 1, 0, 90010, 121),
  (110155, 'Tome of Fireball XI', 1103, 10155, 60, 42000, 10500, 1, 0, 90010, 122),
  (110222, 'Tome of Ice Armor IV', 1103, 10222, 60, 42000, 10500, 1, 0, 90010, 123),
  (110196, 'Tome of Mana Shield VI', 1103, 10196, 60, 42000, 10500, 1, 0, 90010, 124);

-- Mage: 13 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (105499, 'Tome of Blink', 1103, 5499, 20, 0, 0, 3, 0, NULL, NULL),
  (128403, 'Tome of Evocation', 1103, 28403, 20, 0, 0, 3, 0, NULL, NULL),
  (103581, 'Tome of Teleport: Ironforge', 1103, 3581, 20, 0, 0, 3, 0, NULL, NULL),
  (100665, 'Tome of Teleport: Stormwind', 1103, 665, 20, 0, 0, 3, 0, NULL, NULL),
  (103576, 'Tome of Counterspell', 1103, 3576, 24, 0, 0, 3, 0, NULL, NULL),
  (103578, 'Tome of Teleport: Darnassus', 1103, 3578, 30, 0, 0, 3, 0, NULL, NULL),
  (111421, 'Tome of Portal: Ironforge', 1103, 11421, 40, 0, 0, 3, 0, NULL, NULL),
  (101851, 'Tome of Portal: Stormwind', 1103, 1851, 40, 0, 0, 3, 0, NULL, NULL),
  (111422, 'Tome of Portal: Darnassus', 1103, 11422, 50, 0, 0, 3, 0, NULL, NULL),
  (113039, 'Tome of Ice Barrier IV', 1103, 13039, 58, 0, 0, 3, 0, NULL, NULL),
  (113026, 'Tome of Blast Wave V', 1103, 13026, 60, 0, 0, 3, 0, NULL, NULL),
  (112829, 'Tome of Polymorph IV', 1103, 12829, 60, 0, 0, 3, 0, NULL, NULL),
  (113017, 'Tome of Pyroblast VIII', 1103, 13017, 60, 0, 0, 3, 0, NULL, NULL);

-- Warlock: 117 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (101374, 'Grimoire of Immolate I', 1246, 1374, 1, 10, 2, 1, 0, 90011, 0),
  (107763, 'Grimoire of Summon Imp', 1246, 7763, 1, 100, 25, 1, 0, 90011, 1),
  (106221, 'Grimoire of Corruption I', 1246, 6221, 4, 100, 25, 1, 0, 90011, 2),
  (101393, 'Grimoire of Curse of Weakness I', 1246, 1393, 4, 100, 25, 1, 0, 90011, 3),
  (101476, 'Grimoire of Life Tap I', 1246, 1476, 6, 100, 25, 1, 0, 90011, 4),
  (101381, 'Grimoire of Shadow Bolt II', 1246, 1381, 6, 100, 25, 1, 0, 90011, 5),
  (101296, 'Grimoire of Curse of Agony I', 1246, 1296, 8, 200, 50, 1, 0, 90011, 6),
  (105783, 'Grimoire of Fear I', 1246, 5783, 8, 200, 50, 1, 0, 90011, 7),
  (106203, 'Grimoire of Create Healthstone (Minor)', 1246, 6203, 10, 300, 75, 1, 0, 90011, 8),
  (101383, 'Grimoire of Demon Skin II', 1246, 1383, 10, 300, 75, 1, 0, 90011, 9),
  (107662, 'Grimoire of Drain Soul I', 1246, 7662, 10, 300, 75, 1, 0, 90011, 10),
  (101375, 'Grimoire of Immolate II', 1246, 1375, 10, 300, 75, 1, 0, 90011, 11),
  (101394, 'Grimoire of Curse of Weakness II', 1246, 1394, 12, 600, 150, 1, 0, 90011, 12),
  (103704, 'Grimoire of Health Funnel I', 1246, 3704, 12, 600, 150, 1, 0, 90011, 13),
  (101382, 'Grimoire of Shadow Bolt III', 1246, 1382, 12, 600, 150, 1, 0, 90011, 14),
  (106224, 'Grimoire of Corruption II', 1246, 6224, 14, 900, 225, 1, 0, 90011, 15),
  (107650, 'Grimoire of Curse of Recklessness I', 1246, 7650, 14, 900, 225, 1, 0, 90011, 16),
  (101367, 'Grimoire of Drain Life I', 1246, 1367, 14, 900, 225, 1, 0, 90011, 17),
  (101477, 'Grimoire of Life Tap II', 1246, 1477, 16, 1200, 300, 1, 0, 90011, 18),
  (105698, 'Grimoire of Unending Breath', 1246, 5698, 16, 1200, 300, 1, 0, 90011, 19),
  (101297, 'Grimoire of Curse of Agony II', 1246, 1297, 18, 1500, 375, 1, 0, 90011, 20),
  (102945, 'Grimoire of Searing Pain I', 1246, 2945, 18, 1500, 375, 1, 0, 90011, 21),
  (101384, 'Grimoire of Demon Armor I', 1246, 1384, 20, 2000, 500, 1, 0, 90011, 22),
  (103705, 'Grimoire of Health Funnel II', 1246, 3705, 20, 2000, 500, 1, 0, 90011, 23),
  (101376, 'Grimoire of Immolate III', 1246, 1376, 20, 2000, 500, 1, 0, 90011, 24),
  (105741, 'Grimoire of Rain of Fire I', 1246, 5741, 20, 2000, 500, 1, 0, 90011, 25),
  (107663, 'Grimoire of Ritual of Summoning', 1246, 7663, 20, 2000, 500, 1, 0, 90011, 26),
  (101406, 'Grimoire of Shadow Bolt IV', 1246, 1406, 20, 2000, 500, 1, 0, 90011, 27),
  (106204, 'Grimoire of Create Healthstone (Lesser)', 1246, 6204, 22, 2500, 625, 1, 0, 90011, 28),
  (106206, 'Grimoire of Curse of Weakness III', 1246, 6206, 22, 2500, 625, 1, 0, 90011, 29),
  (101368, 'Grimoire of Drain Life II', 1246, 1368, 22, 2500, 625, 1, 0, 90011, 30),
  (106228, 'Grimoire of Eye of Kilrogg', 1246, 6228, 22, 2500, 625, 1, 0, 90011, 31),
  (106225, 'Grimoire of Corruption III', 1246, 6225, 24, 3000, 750, 1, 0, 90011, 32),
  (105139, 'Grimoire of Drain Mana I', 1246, 5139, 24, 3000, 750, 1, 0, 90011, 33),
  (108290, 'Grimoire of Drain Soul II', 1246, 8290, 24, 3000, 750, 1, 0, 90011, 34),
  (105501, 'Grimoire of Sense Demons', 1246, 5501, 24, 3000, 750, 1, 0, 90011, 35),
  (105736, 'Grimoire of Curse of Tongues I', 1246, 5736, 26, 4000, 1000, 1, 0, 90011, 36),
  (102971, 'Grimoire of Detect Lesser Invisibility', 1246, 2971, 26, 4000, 1000, 1, 0, 90011, 37),
  (101478, 'Grimoire of Life Tap III', 1246, 1478, 26, 4000, 1000, 1, 0, 90011, 38),
  (118154, 'Grimoire of Searing Pain II', 1246, 18154, 26, 4000, 1000, 1, 0, 90011, 39),
  (107664, 'Grimoire of Banish I', 1246, 7664, 28, 5000, 1250, 1, 0, 90011, 40),
  (101197, 'Grimoire of Create Firestone (Lesser)', 1246, 1197, 28, 5000, 1250, 1, 0, 90011, 41),
  (106218, 'Grimoire of Curse of Agony III', 1246, 6218, 28, 5000, 1250, 1, 0, 90011, 42),
  (107660, 'Grimoire of Curse of Recklessness II', 1246, 7660, 28, 5000, 1250, 1, 0, 90011, 43),
  (103706, 'Grimoire of Health Funnel III', 1246, 3706, 28, 5000, 1250, 1, 0, 90011, 44),
  (101407, 'Grimoire of Shadow Bolt V', 1246, 1407, 28, 5000, 1250, 1, 0, 90011, 45),
  (101404, 'Grimoire of Demon Armor II', 1246, 1404, 30, 6000, 1500, 1, 0, 90011, 46),
  (101369, 'Grimoire of Drain Life III', 1246, 1369, 30, 6000, 1500, 1, 0, 90011, 47),
  (105709, 'Grimoire of Hellfire I', 1246, 5709, 30, 6000, 1500, 1, 0, 90011, 48),
  (102942, 'Grimoire of Immolate IV', 1246, 2942, 30, 6000, 1500, 1, 0, 90011, 49),
  (107647, 'Grimoire of Curse of Weakness IV', 1246, 7647, 32, 7000, 1750, 1, 0, 90011, 50),
  (107666, 'Grimoire of Curse of the Elements I', 1246, 7666, 32, 7000, 1750, 1, 0, 90011, 51),
  (106214, 'Grimoire of Fear II', 1246, 6214, 32, 7000, 1750, 1, 0, 90011, 52),
  (106232, 'Grimoire of Shadow Ward I', 1246, 6232, 32, 7000, 1750, 1, 0, 90011, 53),
  (107649, 'Grimoire of Corruption IV', 1246, 7649, 34, 8000, 2000, 1, 0, 90011, 54),
  (105700, 'Grimoire of Create Healthstone', 1246, 5700, 34, 8000, 2000, 1, 0, 90011, 55),
  (106227, 'Grimoire of Drain Mana II', 1246, 6227, 34, 8000, 2000, 1, 0, 90011, 56),
  (106220, 'Grimoire of Rain of Fire II', 1246, 6220, 34, 8000, 2000, 1, 0, 90011, 57),
  (118155, 'Grimoire of Searing Pain III', 1246, 18155, 34, 8000, 2000, 1, 0, 90011, 58),
  (100607, 'Grimoire of Create Firestone', 1246, 607, 36, 9000, 2250, 1, 0, 90011, 59),
  (106485, 'Grimoire of Create Spellstone', 1246, 6485, 36, 9000, 2250, 1, 0, 90011, 60),
  (103707, 'Grimoire of Health Funnel IV', 1246, 3707, 36, 9000, 2250, 1, 0, 90011, 61),
  (111690, 'Grimoire of Life Tap IV', 1246, 11690, 36, 9000, 2250, 1, 0, 90011, 62),
  (107642, 'Grimoire of Shadow Bolt VI', 1246, 7642, 36, 9000, 2250, 1, 0, 90011, 63),
  (111714, 'Grimoire of Curse of Agony IV', 1246, 11714, 38, 10000, 2500, 1, 0, 90011, 64),
  (102972, 'Grimoire of Detect Invisibility', 1246, 2972, 38, 10000, 2500, 1, 0, 90011, 65),
  (107652, 'Grimoire of Drain Life IV', 1246, 7652, 38, 10000, 2500, 1, 0, 90011, 66),
  (108291, 'Grimoire of Drain Soul III', 1246, 8291, 38, 10000, 2500, 1, 0, 90011, 67),
  (111736, 'Grimoire of Demon Armor III', 1246, 11736, 40, 11000, 2750, 1, 0, 90011, 68),
  (111666, 'Grimoire of Immolate V', 1246, 11666, 40, 11000, 2750, 1, 0, 90011, 69),
  (107661, 'Grimoire of Curse of Recklessness III', 1246, 7661, 42, 11000, 2750, 1, 0, 90011, 70),
  (111709, 'Grimoire of Curse of Weakness V', 1246, 11709, 42, 11000, 2750, 1, 0, 90011, 71),
  (111685, 'Grimoire of Hellfire II', 1246, 11685, 42, 9900, 2475, 1, 0, 90011, 72),
  (118156, 'Grimoire of Searing Pain IV', 1246, 18156, 42, 11000, 2750, 1, 0, 90011, 73),
  (111741, 'Grimoire of Shadow Ward II', 1246, 11741, 42, 11000, 2750, 1, 0, 90011, 74),
  (111673, 'Grimoire of Corruption V', 1246, 11673, 44, 12000, 3000, 1, 0, 90011, 75),
  (117865, 'Grimoire of Curse of Shadow I', 1246, 17865, 44, 12000, 3000, 1, 0, 90011, 76),
  (111705, 'Grimoire of Drain Mana III', 1246, 11705, 44, 12000, 3000, 1, 0, 90011, 77),
  (111696, 'Grimoire of Health Funnel V', 1246, 11696, 44, 12000, 3000, 1, 0, 90011, 78),
  (111662, 'Grimoire of Shadow Bolt VII', 1246, 11662, 44, 12000, 3000, 1, 0, 90011, 79),
  (118648, 'Grimoire of Banish II', 1246, 18648, 46, 13000, 3250, 1, 0, 90011, 80),
  (118170, 'Grimoire of Create Firestone (Greater)', 1246, 18170, 46, 13000, 3250, 1, 0, 90011, 81),
  (105702, 'Grimoire of Create Healthstone (Greater)', 1246, 5702, 46, 13000, 3250, 1, 0, 90011, 82),
  (111723, 'Grimoire of Curse of the Elements II', 1246, 11723, 46, 13000, 3250, 1, 0, 90011, 83),
  (111701, 'Grimoire of Drain Life V', 1246, 11701, 46, 13000, 3250, 1, 0, 90011, 84),
  (111691, 'Grimoire of Life Tap V', 1246, 11691, 46, 13000, 3250, 1, 0, 90011, 85),
  (111679, 'Grimoire of Rain of Fire III', 1246, 11679, 46, 13000, 3250, 1, 0, 90011, 86),
  (117732, 'Grimoire of Create Spellstone (Greater)', 1246, 17732, 48, 14000, 3500, 1, 0, 90011, 87),
  (111715, 'Grimoire of Curse of Agony V', 1246, 11715, 48, 14000, 3500, 1, 0, 90011, 88),
  (111720, 'Grimoire of Curse of Tongues II', 1246, 11720, 50, 15000, 3750, 1, 0, 90011, 89),
  (111737, 'Grimoire of Demon Armor IV', 1246, 11737, 50, 15000, 3750, 1, 0, 90011, 90),
  (111788, 'Grimoire of Detect Greater Invisibility', 1246, 11788, 50, 15000, 3750, 1, 0, 90011, 91),
  (111669, 'Grimoire of Immolate VI', 1246, 11669, 50, 15000, 3750, 1, 0, 90011, 92),
  (118157, 'Grimoire of Searing Pain V', 1246, 18157, 50, 15000, 3750, 1, 0, 90011, 93),
  (111710, 'Grimoire of Curse of Weakness VI', 1246, 11710, 52, 18000, 4500, 1, 0, 90011, 94),
  (111676, 'Grimoire of Drain Soul IV', 1246, 11676, 52, 18000, 4500, 1, 0, 90011, 95),
  (111697, 'Grimoire of Health Funnel VI', 1246, 11697, 52, 18000, 4500, 1, 0, 90011, 96),
  (111663, 'Grimoire of Shadow Bolt VIII', 1246, 11663, 52, 18000, 4500, 1, 0, 90011, 97),
  (111742, 'Grimoire of Shadow Ward III', 1246, 11742, 52, 18000, 4500, 1, 0, 90011, 98),
  (111674, 'Grimoire of Corruption VI', 1246, 11674, 54, 20000, 5000, 1, 0, 90011, 99),
  (111702, 'Grimoire of Drain Life VI', 1246, 11702, 54, 20000, 5000, 1, 0, 90011, 100),
  (111686, 'Grimoire of Hellfire III', 1246, 11686, 54, 18000, 4500, 1, 0, 90011, 101),
  (118171, 'Grimoire of Create Firestone (Major)', 1246, 18171, 56, 22000, 5500, 1, 0, 90011, 102),
  (111718, 'Grimoire of Curse of Recklessness IV', 1246, 11718, 56, 22000, 5500, 1, 0, 90011, 103),
  (117938, 'Grimoire of Curse of Shadow II', 1246, 17938, 56, 22000, 5500, 1, 0, 90011, 104),
  (106216, 'Grimoire of Fear III', 1246, 6216, 56, 22000, 5500, 1, 0, 90011, 105),
  (111692, 'Grimoire of Life Tap VI', 1246, 11692, 56, 22000, 5500, 1, 0, 90011, 106),
  (111731, 'Grimoire of Create Healthstone (Major)', 1246, 11731, 58, 24000, 6000, 1, 0, 90011, 107),
  (111716, 'Grimoire of Curse of Agony VI', 1246, 11716, 58, 24000, 6000, 1, 0, 90011, 108),
  (111680, 'Grimoire of Rain of Fire IV', 1246, 11680, 58, 24000, 6000, 1, 0, 90011, 109),
  (118158, 'Grimoire of Searing Pain VI', 1246, 18158, 58, 24000, 6000, 1, 0, 90011, 110),
  (117733, 'Grimoire of Create Spellstone (Major)', 1246, 17733, 60, 26000, 6500, 1, 0, 90011, 111),
  (111724, 'Grimoire of Curse of the Elements III', 1246, 11724, 60, 26000, 6500, 1, 0, 90011, 112),
  (111738, 'Grimoire of Demon Armor V', 1246, 11738, 60, 26000, 6500, 1, 0, 90011, 113),
  (111698, 'Grimoire of Health Funnel VII', 1246, 11698, 60, 26000, 6500, 1, 0, 90011, 114),
  (111670, 'Grimoire of Immolate VII', 1246, 11670, 60, 26000, 6500, 1, 0, 90011, 115),
  (111664, 'Grimoire of Shadow Bolt IX', 1246, 11664, 60, 26000, 6500, 1, 0, 90011, 116);

-- Warlock: 13 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (111520, 'Grimoire of Summon Voidwalker', 1246, 11520, 10, 0, 0, 3, 0, NULL, NULL),
  (111519, 'Grimoire of Summon Succubus', 1246, 11519, 20, 0, 0, 3, 0, NULL, NULL),
  (108717, 'Grimoire of Summon Felhunter', 1246, 8717, 30, 0, 0, 3, 0, NULL, NULL),
  (118169, 'Grimoire of Howl of Terror II', 1246, 18169, 54, 0, 0, 3, 0, NULL, NULL),
  (118878, 'Grimoire of Shadowburn VI', 1246, 18878, 56, 0, 0, 3, 0, NULL, NULL),
  (118160, 'Grimoire of Soul Fire II', 1246, 18160, 56, 0, 0, 3, 0, NULL, NULL),
  (118162, 'Grimoire of Death Coil III', 1246, 18162, 58, 0, 0, 3, 0, NULL, NULL),
  (111728, 'Grimoire of Enslave Demon III', 1246, 11728, 58, 0, 0, 3, 0, NULL, NULL),
  (118929, 'Grimoire of Siphon Life IV', 1246, 18929, 58, 0, 0, 3, 0, NULL, NULL),
  (118935, 'Grimoire of Conflagrate IV', 1246, 18935, 60, 0, 0, 3, 0, NULL, NULL),
  (120769, 'Grimoire of Create Soulstone (Major)', 1246, 20769, 60, 0, 0, 3, 0, NULL, NULL),
  (118753, 'Grimoire of Curse of Doom', 1246, 18753, 60, 0, 0, 3, 0, NULL, NULL),
  (118940, 'Grimoire of Dark Pact III', 1246, 18940, 60, 0, 0, 3, 0, NULL, NULL);

-- Priest: 115 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (101255, 'Codex of Power Word: Fortitude I', 1143, 1255, 1, 10, 2, 1, 0, 90012, 0),
  (102056, 'Codex of Lesser Heal II', 1143, 2056, 4, 100, 25, 1, 0, 90012, 1),
  (101258, 'Codex of Shadow Word: Pain I', 1143, 1258, 4, 100, 25, 1, 0, 90012, 2),
  (102851, 'Codex of Power Word: Shield I', 1143, 2851, 6, 100, 25, 1, 0, 90012, 3),
  (101275, 'Codex of Smite II', 1143, 1275, 6, 100, 25, 1, 0, 90012, 4),
  (101265, 'Codex of Fade I', 1143, 1265, 8, 200, 50, 1, 0, 90012, 5),
  (106073, 'Codex of Renew I', 1143, 6073, 8, 200, 50, 1, 0, 90012, 6),
  (102057, 'Codex of Lesser Heal III', 1143, 2057, 10, 300, 75, 1, 0, 90012, 7),
  (108093, 'Codex of Mind Blast I', 1143, 8093, 10, 300, 75, 1, 0, 90012, 8),
  (102013, 'Codex of Resurrection I', 1143, 2013, 10, 300, 75, 1, 0, 90012, 9),
  (101259, 'Codex of Shadow Word: Pain II', 1143, 1259, 10, 300, 75, 1, 0, 90012, 10),
  (101252, 'Codex of Inner Fire I', 1143, 1252, 12, 800, 200, 1, 0, 90012, 11),
  (101256, 'Codex of Power Word: Fortitude II', 1143, 1256, 12, 800, 200, 1, 0, 90012, 12),
  (101277, 'Codex of Power Word: Shield II', 1143, 1277, 12, 800, 200, 1, 0, 90012, 13),
  (101268, 'Codex of Cure Disease', 1143, 1268, 14, 1200, 300, 1, 0, 90012, 14),
  (106079, 'Codex of Renew II', 1143, 6079, 14, 1200, 300, 1, 0, 90012, 15),
  (101276, 'Codex of Smite III', 1143, 1276, 14, 1200, 300, 1, 0, 90012, 16),
  (102058, 'Codex of Heal I', 1143, 2058, 16, 1600, 400, 1, 0, 90012, 17),
  (108107, 'Codex of Mind Blast II', 1143, 8107, 16, 1600, 400, 1, 0, 90012, 18),
  (101283, 'Codex of Dispel Magic I', 1143, 1283, 18, 2000, 500, 1, 0, 90012, 19),
  (101278, 'Codex of Power Word: Shield III', 1143, 1278, 18, 2000, 500, 1, 0, 90012, 20),
  (101260, 'Codex of Shadow Word: Pain III', 1143, 1260, 18, 2000, 500, 1, 0, 90012, 21),
  (109580, 'Codex of Fade II', 1143, 9580, 20, 3000, 750, 1, 0, 90012, 22),
  (102066, 'Codex of Flash Heal I', 1143, 2066, 20, 3000, 750, 1, 0, 90012, 23),
  (127796, 'Codex of Holy Fire I', 1143, 27796, 20, 3000, 750, 1, 0, 90012, 24),
  (107130, 'Codex of Inner Fire II', 1143, 7130, 20, 3000, 750, 1, 0, 90012, 25),
  (108126, 'Codex of Mind Soothe I', 1143, 8126, 20, 3000, 750, 1, 0, 90012, 26),
  (106080, 'Codex of Renew III', 1143, 6080, 20, 3000, 750, 1, 0, 90012, 27),
  (101425, 'Codex of Shackle Undead I', 1143, 1425, 20, 3000, 750, 1, 0, 90012, 28),
  (102059, 'Codex of Heal II', 1143, 2059, 22, 4000, 1000, 1, 0, 90012, 29),
  (108108, 'Codex of Mind Blast III', 1143, 8108, 22, 4000, 1000, 1, 0, 90012, 30),
  (102097, 'Codex of Mind Vision I', 1143, 2097, 22, 4000, 1000, 1, 0, 90012, 31),
  (102016, 'Codex of Resurrection II', 1143, 2016, 22, 4000, 1000, 1, 0, 90012, 32),
  (101300, 'Codex of Smite IV', 1143, 1300, 22, 4000, 1000, 1, 0, 90012, 33),
  (115452, 'Codex of Holy Fire II', 1143, 15452, 24, 5000, 1250, 1, 0, 90012, 34),
  (108130, 'Codex of Mana Burn I', 1143, 8130, 24, 5000, 1250, 1, 0, 90012, 35),
  (101257, 'Codex of Power Word: Fortitude III', 1143, 1257, 24, 5000, 1250, 1, 0, 90012, 36),
  (101298, 'Codex of Power Word: Shield IV', 1143, 1298, 24, 5000, 1250, 1, 0, 90012, 37),
  (109475, 'Codex of Flash Heal II', 1143, 9475, 26, 6000, 1500, 1, 0, 90012, 38),
  (106081, 'Codex of Renew IV', 1143, 6081, 26, 6000, 1500, 1, 0, 90012, 39),
  (101261, 'Codex of Shadow Word: Pain IV', 1143, 1261, 26, 6000, 1500, 1, 0, 90012, 40),
  (106071, 'Codex of Heal III', 1143, 6071, 28, 8000, 2000, 1, 0, 90012, 41),
  (108109, 'Codex of Mind Blast IV', 1143, 8109, 28, 8000, 2000, 1, 0, 90012, 42),
  (109581, 'Codex of Fade III', 1143, 9581, 30, 10000, 2500, 1, 0, 90012, 43),
  (115454, 'Codex of Holy Fire III', 1143, 15454, 30, 10000, 2500, 1, 0, 90012, 44),
  (101253, 'Codex of Inner Fire III', 1143, 1253, 30, 10000, 2500, 1, 0, 90012, 45),
  (106067, 'Codex of Power Word: Shield V', 1143, 6067, 30, 10000, 2500, 1, 0, 90012, 46),
  (101287, 'Codex of Prayer of Healing I', 1143, 1287, 30, 10000, 2500, 1, 0, 90012, 47),
  (101279, 'Codex of Shadow Protection I', 1143, 1279, 30, 10000, 2500, 1, 0, 90012, 48),
  (101301, 'Codex of Smite V', 1143, 1301, 30, 10000, 2500, 1, 0, 90012, 49),
  (101269, 'Codex of Abolish Disease', 1143, 1269, 32, 11000, 2750, 1, 0, 90012, 50),
  (109476, 'Codex of Flash Heal III', 1143, 9476, 32, 11000, 2750, 1, 0, 90012, 51),
  (108132, 'Codex of Mana Burn II', 1143, 8132, 32, 11000, 2750, 1, 0, 90012, 52),
  (106082, 'Codex of Renew V', 1143, 6082, 32, 11000, 2750, 1, 0, 90012, 53),
  (106072, 'Codex of Heal IV', 1143, 6072, 34, 12000, 3000, 1, 0, 90012, 54),
  (106492, 'Codex of Levitate', 1143, 6492, 34, 12000, 3000, 1, 0, 90012, 55),
  (108110, 'Codex of Mind Blast V', 1143, 8110, 34, 12000, 3000, 1, 0, 90012, 56),
  (110882, 'Codex of Resurrection III', 1143, 10882, 34, 12000, 3000, 1, 0, 90012, 57),
  (102799, 'Codex of Shadow Word: Pain V', 1143, 2799, 34, 12000, 3000, 1, 0, 90012, 58),
  (101284, 'Codex of Dispel Magic II', 1143, 1284, 36, 14000, 3500, 1, 0, 90012, 59),
  (115455, 'Codex of Holy Fire IV', 1143, 15455, 36, 14000, 3500, 1, 0, 90012, 60),
  (108193, 'Codex of Mind Soothe II', 1143, 8193, 36, 14000, 3500, 1, 0, 90012, 61),
  (102793, 'Codex of Power Word: Fortitude IV', 1143, 2793, 36, 14000, 3500, 1, 0, 90012, 62),
  (106068, 'Codex of Power Word: Shield VI', 1143, 6068, 36, 14000, 3500, 1, 0, 90012, 63),
  (109477, 'Codex of Flash Heal IV', 1143, 9477, 38, 16000, 4000, 1, 0, 90012, 64),
  (106083, 'Codex of Renew VI', 1143, 6083, 38, 16000, 4000, 1, 0, 90012, 65),
  (106062, 'Codex of Smite VI', 1143, 6062, 38, 16000, 4000, 1, 0, 90012, 66),
  (109593, 'Codex of Fade IV', 1143, 9593, 40, 18000, 4500, 1, 0, 90012, 67),
  (102065, 'Codex of Greater Heal I', 1143, 2065, 40, 18000, 4500, 1, 0, 90012, 68),
  (101254, 'Codex of Inner Fire IV', 1143, 1254, 40, 18000, 4500, 1, 0, 90012, 69),
  (110877, 'Codex of Mana Burn III', 1143, 10877, 40, 18000, 4500, 1, 0, 90012, 70),
  (108111, 'Codex of Mind Blast VI', 1143, 8111, 40, 18000, 4500, 1, 0, 90012, 71),
  (101288, 'Codex of Prayer of Healing II', 1143, 1288, 40, 18000, 4500, 1, 0, 90012, 72),
  (109486, 'Codex of Shackle Undead II', 1143, 9486, 40, 18000, 4500, 1, 0, 90012, 73),
  (115457, 'Codex of Holy Fire V', 1143, 15457, 42, 22000, 5500, 1, 0, 90012, 74),
  (110902, 'Codex of Power Word: Shield VII', 1143, 10902, 42, 22000, 5500, 1, 0, 90012, 75),
  (101280, 'Codex of Shadow Protection II', 1143, 1280, 42, 22000, 5500, 1, 0, 90012, 76),
  (110895, 'Codex of Shadow Word: Pain VI', 1143, 10895, 42, 22000, 5500, 1, 0, 90012, 77),
  (110918, 'Codex of Flash Heal V', 1143, 10918, 44, 24000, 6000, 1, 0, 90012, 78),
  (110910, 'Codex of Mind Vision II', 1143, 10910, 44, 24000, 6000, 1, 0, 90012, 79),
  (110930, 'Codex of Renew VII', 1143, 10930, 44, 24000, 6000, 1, 0, 90012, 80),
  (102069, 'Codex of Greater Heal II', 1143, 2069, 46, 26000, 6500, 1, 0, 90012, 81),
  (110948, 'Codex of Mind Blast VII', 1143, 10948, 46, 26000, 6500, 1, 0, 90012, 82),
  (110883, 'Codex of Resurrection IV', 1143, 10883, 46, 26000, 6500, 1, 0, 90012, 83),
  (110935, 'Codex of Smite VII', 1143, 10935, 46, 26000, 6500, 1, 0, 90012, 84),
  (115459, 'Codex of Holy Fire VI', 1143, 15459, 48, 28000, 7000, 1, 0, 90012, 85),
  (110878, 'Codex of Mana Burn IV', 1143, 10878, 48, 28000, 7000, 1, 0, 90012, 86),
  (110939, 'Codex of Power Word: Fortitude V', 1143, 10939, 48, 28000, 7000, 1, 0, 90012, 87),
  (110903, 'Codex of Power Word: Shield VIII', 1143, 10903, 48, 28000, 7000, 1, 0, 90012, 88),
  (110943, 'Codex of Fade V', 1143, 10943, 50, 30000, 7500, 1, 0, 90012, 89),
  (110919, 'Codex of Flash Heal VI', 1143, 10919, 50, 30000, 7500, 1, 0, 90012, 90),
  (111025, 'Codex of Inner Fire V', 1143, 11025, 50, 30000, 7500, 1, 0, 90012, 91),
  (102049, 'Codex of Prayer of Healing III', 1143, 2049, 50, 30000, 7500, 1, 0, 90012, 92),
  (110931, 'Codex of Renew VIII', 1143, 10931, 50, 30000, 7500, 1, 0, 90012, 93),
  (110896, 'Codex of Shadow Word: Pain VII', 1143, 10896, 50, 30000, 7500, 1, 0, 90012, 94),
  (102067, 'Codex of Greater Heal III', 1143, 2067, 52, 38000, 9500, 1, 0, 90012, 95),
  (110949, 'Codex of Mind Blast VIII', 1143, 10949, 52, 38000, 9500, 1, 0, 90012, 96),
  (110954, 'Codex of Mind Soothe III', 1143, 10954, 52, 38000, 9500, 1, 0, 90012, 97),
  (115460, 'Codex of Holy Fire VII', 1143, 15460, 54, 40000, 10000, 1, 0, 90012, 98),
  (110904, 'Codex of Power Word: Shield IX', 1143, 10904, 54, 40000, 10000, 1, 0, 90012, 99),
  (110936, 'Codex of Smite VIII', 1143, 10936, 54, 40000, 10000, 1, 0, 90012, 100),
  (110920, 'Codex of Flash Heal VII', 1143, 10920, 56, 42000, 10500, 1, 0, 90012, 101),
  (110879, 'Codex of Mana Burn V', 1143, 10879, 56, 42000, 10500, 1, 0, 90012, 102),
  (110932, 'Codex of Renew IX', 1143, 10932, 56, 42000, 10500, 1, 0, 90012, 103),
  (110959, 'Codex of Shadow Protection III', 1143, 10959, 56, 42000, 10500, 1, 0, 90012, 104),
  (102068, 'Codex of Greater Heal IV', 1143, 2068, 58, 44000, 11000, 1, 0, 90012, 105),
  (110950, 'Codex of Mind Blast IX', 1143, 10950, 58, 44000, 11000, 1, 0, 90012, 106),
  (120771, 'Codex of Resurrection V', 1143, 20771, 58, 44000, 11000, 1, 0, 90012, 107),
  (110897, 'Codex of Shadow Word: Pain VIII', 1143, 10897, 58, 44000, 11000, 1, 0, 90012, 108),
  (110944, 'Codex of Fade VI', 1143, 10944, 60, 46000, 11500, 1, 0, 90012, 109),
  (118806, 'Codex of Holy Fire VIII', 1143, 18806, 60, 46000, 11500, 1, 0, 90012, 110),
  (111026, 'Codex of Inner Fire VI', 1143, 11026, 60, 46000, 11500, 1, 0, 90012, 111),
  (110940, 'Codex of Power Word: Fortitude VI', 1143, 10940, 60, 46000, 11500, 1, 0, 90012, 112),
  (110905, 'Codex of Power Word: Shield X', 1143, 10905, 60, 46000, 11500, 1, 0, 90012, 113),
  (110962, 'Codex of Prayer of Healing IV', 1143, 10962, 60, 46000, 11500, 1, 0, 90012, 114);

-- Priest: 9 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (110891, 'Codex of Psychic Scream IV', 1143, 10891, 56, 0, 0, 3, 0, NULL, NULL),
  (110914, 'Codex of Mind Control III', 1143, 10914, 58, 0, 0, 3, 0, NULL, NULL),
  (119356, 'Codex of Starshards VII', 1143, 19356, 58, 0, 0, 3, 0, NULL, NULL),
  (127843, 'Codex of Divine Spirit IV', 1143, 27843, 60, 0, 0, 3, 0, NULL, NULL),
  (119361, 'Codex of Elune''s Grace V', 1143, 19361, 60, 0, 0, 3, 0, NULL, NULL),
  (127823, 'Codex of Holy Nova VI', 1143, 27823, 60, 0, 0, 3, 0, NULL, NULL),
  (127876, 'Codex of Lightwell III', 1143, 27876, 60, 0, 0, 3, 0, NULL, NULL),
  (118808, 'Codex of Mind Flay VI', 1143, 18808, 60, 0, 0, 3, 0, NULL, NULL),
  (127845, 'Codex of Prayer of Spirit I', 1143, 27845, 60, 0, 0, 3, 0, NULL, NULL);

-- Paladin: 102 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (101875, 'Libram of Devotion Aura I', 1155, 1875, 1, 10, 2, 1, 0, 90013, 0),
  (119741, 'Libram of Blessing of Might I', 1155, 19741, 4, 100, 25, 1, 0, 90013, 1),
  (110321, 'Libram of Judgement', 1155, 10321, 4, 100, 25, 1, 0, 90013, 2),
  (101873, 'Libram of Holy Light II', 1155, 1873, 6, 100, 25, 1, 0, 90013, 3),
  (121083, 'Libram of Seal of the Crusader I', 1155, 21083, 6, 100, 25, 1, 0, 90013, 4),
  (101937, 'Libram of Purify', 1155, 1937, 8, 100, 25, 1, 0, 90013, 5),
  (110294, 'Libram of Devotion Aura II', 1155, 10294, 10, 300, 75, 1, 0, 90013, 6),
  (120437, 'Libram of Seal of Righteousness II', 1155, 20437, 10, 300, 75, 1, 0, 90013, 7),
  (119839, 'Libram of Blessing of Might II', 1155, 19839, 12, 1000, 250, 1, 0, 90013, 8),
  (120444, 'Libram of Seal of the Crusader II', 1155, 20444, 12, 1000, 250, 1, 0, 90013, 9),
  (119743, 'Libram of Blessing of Wisdom I', 1155, 19743, 14, 2000, 500, 1, 0, 90013, 10),
  (101874, 'Libram of Holy Light III', 1155, 1874, 14, 2000, 500, 1, 0, 90013, 11),
  (107296, 'Libram of Retribution Aura I', 1155, 7296, 16, 3000, 750, 1, 0, 90013, 12),
  (120450, 'Libram of Righteous Fury', 1155, 20450, 16, 3000, 750, 1, 0, 90013, 13),
  (101909, 'Libram of Blessing of Freedom', 1155, 1909, 18, 3500, 875, 1, 0, 90013, 14),
  (120438, 'Libram of Seal of Righteousness III', 1155, 20438, 18, 3500, 875, 1, 0, 90013, 15),
  (101876, 'Libram of Devotion Aura III', 1155, 1876, 20, 4000, 1000, 1, 0, 90013, 16),
  (105613, 'Libram of Exorcism I', 1155, 5613, 20, 4000, 1000, 1, 0, 90013, 17),
  (119751, 'Libram of Flash of Light I', 1155, 19751, 20, 4000, 1000, 1, 0, 90013, 18),
  (119840, 'Libram of Blessing of Might III', 1155, 19840, 22, 4000, 1000, 1, 0, 90013, 19),
  (119747, 'Libram of Concentration Aura', 1155, 19747, 22, 4000, 1000, 1, 0, 90013, 20),
  (101913, 'Libram of Holy Light IV', 1155, 1913, 22, 4000, 1000, 1, 0, 90013, 21),
  (120462, 'Libram of Seal of Justice', 1155, 20462, 22, 4000, 1000, 1, 0, 90013, 22),
  (120445, 'Libram of Seal of the Crusader III', 1155, 20445, 22, 4000, 1000, 1, 0, 90013, 23),
  (119855, 'Libram of Blessing of Wisdom II', 1155, 19855, 24, 5000, 1250, 1, 0, 90013, 24),
  (110323, 'Libram of Redemption II', 1155, 10323, 24, 5000, 1250, 1, 0, 90013, 25),
  (105253, 'Libram of Turn Undead I', 1155, 5253, 24, 5000, 1250, 1, 0, 90013, 26),
  (101912, 'Libram of Blessing of Salvation', 1155, 1912, 26, 6000, 1500, 1, 0, 90013, 27),
  (119944, 'Libram of Flash of Light II', 1155, 19944, 26, 6000, 1500, 1, 0, 90013, 28),
  (110302, 'Libram of Retribution Aura II', 1155, 10302, 26, 6000, 1500, 1, 0, 90013, 29),
  (120439, 'Libram of Seal of Righteousness IV', 1155, 20439, 26, 6000, 1500, 1, 0, 90013, 30),
  (105616, 'Libram of Exorcism II', 1155, 5616, 28, 9000, 2250, 1, 0, 90013, 31),
  (119892, 'Libram of Shadow Resistance Aura I', 1155, 19892, 28, 9000, 2250, 1, 0, 90013, 32),
  (110295, 'Libram of Devotion Aura IV', 1155, 10295, 30, 11000, 2750, 1, 0, 90013, 33),
  (101914, 'Libram of Holy Light V', 1155, 1914, 30, 11000, 2750, 1, 0, 90013, 34),
  (120455, 'Libram of Seal of Light I', 1155, 20455, 30, 11000, 2750, 1, 0, 90013, 35),
  (119841, 'Libram of Blessing of Might IV', 1155, 19841, 32, 12000, 3000, 1, 0, 90013, 36),
  (119893, 'Libram of Frost Resistance Aura I', 1155, 19893, 32, 12000, 3000, 1, 0, 90013, 37),
  (120446, 'Libram of Seal of the Crusader IV', 1155, 20446, 32, 12000, 3000, 1, 0, 90013, 38),
  (119856, 'Libram of Blessing of Wisdom III', 1155, 19856, 34, 13000, 3250, 1, 0, 90013, 39),
  (119945, 'Libram of Flash of Light III', 1155, 19945, 34, 13000, 3250, 1, 0, 90013, 40),
  (120440, 'Libram of Seal of Righteousness V', 1155, 20440, 34, 13000, 3250, 1, 0, 90013, 41),
  (105617, 'Libram of Exorcism III', 1155, 5617, 36, 14000, 3500, 1, 0, 90013, 42),
  (119894, 'Libram of Fire Resistance Aura I', 1155, 19894, 36, 14000, 3500, 1, 0, 90013, 43),
  (110325, 'Libram of Redemption III', 1155, 10325, 36, 14000, 3500, 1, 0, 90013, 44),
  (110303, 'Libram of Retribution Aura III', 1155, 10303, 36, 14000, 3500, 1, 0, 90013, 45),
  (103473, 'Libram of Holy Light VI', 1155, 3473, 38, 16000, 4000, 1, 0, 90013, 46),
  (120459, 'Libram of Seal of Wisdom I', 1155, 20459, 38, 16000, 4000, 1, 0, 90013, 47),
  (105629, 'Libram of Turn Undead II', 1155, 5629, 38, 16000, 4000, 1, 0, 90013, 48),
  (119995, 'Libram of Blessing of Light I', 1155, 19995, 40, 20000, 5000, 1, 0, 90013, 49),
  (101877, 'Libram of Devotion Aura V', 1155, 1877, 40, 20000, 5000, 1, 0, 90013, 50),
  (120456, 'Libram of Seal of Light II', 1155, 20456, 40, 20000, 5000, 1, 0, 90013, 51),
  (119904, 'Libram of Shadow Resistance Aura II', 1155, 19904, 40, 20000, 5000, 1, 0, 90013, 52),
  (119842, 'Libram of Blessing of Might V', 1155, 19842, 42, 21000, 5250, 1, 0, 90013, 53),
  (104990, 'Libram of Cleanse', 1155, 4990, 42, 21000, 5250, 1, 0, 90013, 54),
  (119946, 'Libram of Flash of Light IV', 1155, 19946, 42, 21000, 5250, 1, 0, 90013, 55),
  (120441, 'Libram of Seal of Righteousness VI', 1155, 20441, 42, 21000, 5250, 1, 0, 90013, 56),
  (120447, 'Libram of Seal of the Crusader V', 1155, 20447, 42, 21000, 5250, 1, 0, 90013, 57),
  (119857, 'Libram of Blessing of Wisdom IV', 1155, 19857, 44, 22000, 5500, 1, 0, 90013, 58),
  (110315, 'Libram of Exorcism IV', 1155, 10315, 44, 22000, 5500, 1, 0, 90013, 59),
  (119906, 'Libram of Frost Resistance Aura II', 1155, 19906, 44, 22000, 5500, 1, 0, 90013, 60),
  (124276, 'Libram of Hammer of Wrath I', 1155, 24276, 44, 22000, 5500, 1, 0, 90013, 61),
  (106941, 'Libram of Blessing of Sacrifice I', 1155, 6941, 46, 24000, 6000, 1, 0, 90013, 62),
  (110330, 'Libram of Holy Light VII', 1155, 10330, 46, 24000, 6000, 1, 0, 90013, 63),
  (110304, 'Libram of Retribution Aura IV', 1155, 10304, 46, 24000, 6000, 1, 0, 90013, 64),
  (119908, 'Libram of Fire Resistance Aura II', 1155, 19908, 48, 26000, 6500, 1, 0, 90013, 65),
  (120774, 'Libram of Redemption IV', 1155, 20774, 48, 26000, 6500, 1, 0, 90013, 66),
  (120460, 'Libram of Seal of Wisdom II', 1155, 20460, 48, 26000, 6500, 1, 0, 90013, 67),
  (119996, 'Libram of Blessing of Light II', 1155, 19996, 50, 28000, 7000, 1, 0, 90013, 68),
  (110296, 'Libram of Devotion Aura VI', 1155, 10296, 50, 28000, 7000, 1, 0, 90013, 69),
  (119947, 'Libram of Flash of Light V', 1155, 19947, 50, 28000, 7000, 1, 0, 90013, 70),
  (100685, 'Libram of Holy Wrath I', 1155, 685, 50, 28000, 7000, 1, 0, 90013, 71),
  (120457, 'Libram of Seal of Light III', 1155, 20457, 50, 28000, 7000, 1, 0, 90013, 72),
  (120442, 'Libram of Seal of Righteousness VII', 1155, 20442, 50, 28000, 7000, 1, 0, 90013, 73),
  (119843, 'Libram of Blessing of Might VI', 1155, 19843, 52, 34000, 8500, 1, 0, 90013, 74),
  (110316, 'Libram of Exorcism V', 1155, 10316, 52, 34000, 8500, 1, 0, 90013, 75),
  (125915, 'Libram of Greater Blessing of Might I', 1155, 25915, 52, 46000, 11500, 1, 0, 90013, 76),
  (124277, 'Libram of Hammer of Wrath II', 1155, 24277, 52, 34000, 8500, 1, 0, 90013, 77),
  (120448, 'Libram of Seal of the Crusader VI', 1155, 20448, 52, 34000, 8500, 1, 0, 90013, 78),
  (119905, 'Libram of Shadow Resistance Aura III', 1155, 19905, 52, 34000, 8500, 1, 0, 90013, 79),
  (110327, 'Libram of Turn Undead III', 1155, 10327, 52, 34000, 8500, 1, 0, 90013, 80),
  (120730, 'Libram of Blessing of Sacrifice II', 1155, 20730, 54, 40000, 10000, 1, 0, 90013, 81),
  (119858, 'Libram of Blessing of Wisdom V', 1155, 19858, 54, 40000, 10000, 1, 0, 90013, 82),
  (125919, 'Libram of Greater Blessing of Wisdom I', 1155, 25919, 54, 40000, 10000, 1, 0, 90013, 83),
  (110331, 'Libram of Holy Light VIII', 1155, 10331, 54, 40000, 10000, 1, 0, 90013, 84),
  (119907, 'Libram of Frost Resistance Aura III', 1155, 19907, 56, 42000, 10500, 1, 0, 90013, 85),
  (110305, 'Libram of Retribution Aura V', 1155, 10305, 56, 42000, 10500, 1, 0, 90013, 86),
  (119948, 'Libram of Flash of Light VI', 1155, 19948, 58, 44000, 11000, 1, 0, 90013, 87),
  (120443, 'Libram of Seal of Righteousness VIII', 1155, 20443, 58, 44000, 11000, 1, 0, 90013, 88),
  (120461, 'Libram of Seal of Wisdom III', 1155, 20461, 58, 44000, 11000, 1, 0, 90013, 89),
  (119997, 'Libram of Blessing of Light III', 1155, 19997, 60, 46000, 11500, 1, 0, 90013, 90),
  (110297, 'Libram of Devotion Aura VII', 1155, 10297, 60, 46000, 11500, 1, 0, 90013, 91),
  (110317, 'Libram of Exorcism VI', 1155, 10317, 60, 46000, 11500, 1, 0, 90013, 92),
  (119909, 'Libram of Fire Resistance Aura III', 1155, 19909, 60, 46000, 11500, 1, 0, 90013, 93),
  (125948, 'Libram of Greater Blessing of Light I', 1155, 25948, 60, 46000, 11500, 1, 0, 90013, 94),
  (125917, 'Libram of Greater Blessing of Might II', 1155, 25917, 60, 41400, 10350, 1, 0, 90013, 95),
  (125939, 'Libram of Greater Blessing of Salvation', 1155, 25939, 60, 46000, 11500, 1, 0, 90013, 96),
  (125920, 'Libram of Greater Blessing of Wisdom II', 1155, 25920, 60, 46000, 11500, 1, 0, 90013, 97),
  (124278, 'Libram of Hammer of Wrath III', 1155, 24278, 60, 46000, 11500, 1, 0, 90013, 98),
  (110320, 'Libram of Holy Wrath II', 1155, 10320, 60, 46000, 11500, 1, 0, 90013, 99),
  (120775, 'Libram of Redemption V', 1155, 20775, 60, 46000, 11500, 1, 0, 90013, 100),
  (120458, 'Libram of Seal of Light IV', 1155, 20458, 60, 46000, 11500, 1, 0, 90013, 101);

-- Paladin: 13 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (105574, 'Libram of Divine Protection II', 1155, 5574, 18, 0, 0, 3, 0, NULL, NULL),
  (119754, 'Libram of Divine Intervention', 1155, 19754, 30, 0, 0, 3, 0, NULL, NULL),
  (110279, 'Libram of Blessing of Protection III', 1155, 10279, 38, 0, 0, 3, 0, NULL, NULL),
  (101898, 'Libram of Divine Shield II', 1155, 1898, 50, 0, 0, 3, 0, NULL, NULL),
  (110311, 'Libram of Lay on Hands III', 1155, 10311, 50, 0, 0, 3, 0, NULL, NULL),
  (110309, 'Libram of Hammer of Justice IV', 1155, 10309, 54, 0, 0, 3, 0, NULL, NULL),
  (120960, 'Libram of Holy Shock III', 1155, 20960, 56, 0, 0, 3, 0, NULL, NULL),
  (120951, 'Libram of Blessing of Sanctuary IV', 1155, 20951, 60, 0, 0, 3, 0, NULL, NULL),
  (120954, 'Libram of Consecration V', 1155, 20954, 60, 0, 0, 3, 0, NULL, NULL),
  (125946, 'Libram of Greater Blessing of Kings', 1155, 25946, 60, 0, 0, 3, 0, NULL, NULL),
  (125951, 'Libram of Greater Blessing of Sanctuary I', 1155, 25951, 60, 0, 0, 3, 0, NULL, NULL),
  (120957, 'Libram of Holy Shield III', 1155, 20957, 60, 0, 0, 3, 0, NULL, NULL),
  (120947, 'Libram of Seal of Command V', 1155, 20947, 60, 0, 0, 3, 0, NULL, NULL);

-- Druid: 153 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (105231, 'Book of Mark of the Wild I', 1317, 5231, 1, 10, 2, 1, 0, 90014, 0),
  (108922, 'Book of Moonfire I', 1317, 8922, 4, 100, 25, 1, 0, 90014, 1),
  (101428, 'Book of Rejuvenation I', 1317, 1428, 4, 100, 25, 1, 0, 90014, 2),
  (101420, 'Book of Thorns I', 1317, 1420, 6, 100, 25, 1, 0, 90014, 3),
  (105181, 'Book of Wrath II', 1317, 5181, 6, 100, 25, 1, 0, 90014, 4),
  (101435, 'Book of Entangling Roots I', 1317, 1435, 8, 200, 50, 1, 0, 90014, 5),
  (105190, 'Book of Healing Touch II', 1317, 5190, 8, 200, 50, 1, 0, 90014, 6),
  (105233, 'Book of Mark of the Wild II', 1317, 5233, 10, 300, 75, 1, 0, 90014, 7),
  (108930, 'Book of Moonfire II', 1317, 8930, 10, 300, 75, 1, 0, 90014, 8),
  (101429, 'Book of Rejuvenation II', 1317, 1429, 10, 300, 75, 1, 0, 90014, 9),
  (108937, 'Book of Regrowth I', 1317, 8937, 12, 800, 200, 1, 0, 90014, 10),
  (105192, 'Book of Healing Touch III', 1317, 5192, 14, 900, 225, 1, 0, 90014, 11),
  (101421, 'Book of Thorns II', 1317, 1421, 14, 900, 225, 1, 0, 90014, 12),
  (105182, 'Book of Wrath III', 1317, 5182, 14, 900, 225, 1, 0, 90014, 13),
  (108931, 'Book of Moonfire III', 1317, 8931, 16, 1800, 450, 1, 0, 90014, 14),
  (101431, 'Book of Rejuvenation III', 1317, 1431, 16, 1800, 450, 1, 0, 90014, 15),
  (101436, 'Book of Entangling Roots II', 1317, 1436, 18, 1900, 475, 1, 0, 90014, 16),
  (101414, 'Book of Faerie Fire I', 1317, 1414, 18, 1900, 475, 1, 0, 90014, 17),
  (105299, 'Book of Hibernate I', 1317, 5299, 18, 1900, 475, 1, 0, 90014, 18),
  (108942, 'Book of Regrowth II', 1317, 8942, 18, 1900, 475, 1, 0, 90014, 19),
  (105193, 'Book of Healing Touch IV', 1317, 5193, 20, 2000, 500, 1, 0, 90014, 20),
  (105235, 'Book of Mark of the Wild III', 1317, 5235, 20, 2000, 500, 1, 0, 90014, 21),
  (102914, 'Book of Starfire I', 1317, 2914, 20, 2000, 500, 1, 0, 90014, 22),
  (108932, 'Book of Moonfire IV', 1317, 8932, 22, 3000, 750, 1, 0, 90014, 23),
  (102092, 'Book of Rejuvenation IV', 1317, 2092, 22, 3000, 750, 1, 0, 90014, 24),
  (102910, 'Book of Soothe Animal I', 1317, 2910, 22, 3000, 750, 1, 0, 90014, 25),
  (105183, 'Book of Wrath IV', 1317, 5183, 22, 3000, 750, 1, 0, 90014, 26),
  (108943, 'Book of Regrowth III', 1317, 8943, 24, 4000, 1000, 1, 0, 90014, 27),
  (102788, 'Book of Remove Curse', 1317, 2788, 24, 4000, 1000, 1, 0, 90014, 28),
  (101422, 'Book of Thorns III', 1317, 1422, 24, 4000, 1000, 1, 0, 90014, 29),
  (102897, 'Book of Abolish Poison', 1317, 2897, 26, 4500, 1125, 1, 0, 90014, 30),
  (105194, 'Book of Healing Touch V', 1317, 5194, 26, 4500, 1125, 1, 0, 90014, 31),
  (108952, 'Book of Starfire II', 1317, 8952, 26, 4500, 1125, 1, 0, 90014, 32),
  (102919, 'Book of Entangling Roots III', 1317, 2919, 28, 5000, 1250, 1, 0, 90014, 33),
  (108933, 'Book of Moonfire V', 1317, 8933, 28, 5000, 1250, 1, 0, 90014, 34),
  (102093, 'Book of Rejuvenation V', 1317, 2093, 28, 5000, 1250, 1, 0, 90014, 35),
  (101415, 'Book of Faerie Fire II', 1317, 1415, 30, 6000, 1500, 1, 0, 90014, 36),
  (106782, 'Book of Mark of the Wild IV', 1317, 6782, 30, 6000, 1500, 1, 0, 90014, 37),
  (108944, 'Book of Regrowth IV', 1317, 8944, 30, 6000, 1500, 1, 0, 90014, 38),
  (105184, 'Book of Wrath V', 1317, 5184, 30, 6000, 1500, 1, 0, 90014, 39),
  (106779, 'Book of Healing Touch VI', 1317, 6779, 32, 8000, 2000, 1, 0, 90014, 40),
  (108934, 'Book of Moonfire VI', 1317, 8934, 34, 10000, 2500, 1, 0, 90014, 41),
  (103628, 'Book of Rejuvenation VI', 1317, 3628, 34, 10000, 2500, 1, 0, 90014, 42),
  (108953, 'Book of Starfire III', 1317, 8953, 34, 10000, 2500, 1, 0, 90014, 43),
  (108915, 'Book of Thorns IV', 1317, 8915, 34, 10000, 2500, 1, 0, 90014, 44),
  (108945, 'Book of Regrowth V', 1317, 8945, 36, 11000, 2750, 1, 0, 90014, 45),
  (102920, 'Book of Entangling Roots IV', 1317, 2920, 38, 12000, 3000, 1, 0, 90014, 46),
  (108904, 'Book of Healing Touch VII', 1317, 8904, 38, 12000, 3000, 1, 0, 90014, 47),
  (118659, 'Book of Hibernate II', 1317, 18659, 38, 12000, 3000, 1, 0, 90014, 48),
  (108956, 'Book of Soothe Animal II', 1317, 8956, 38, 12000, 3000, 1, 0, 90014, 49),
  (106781, 'Book of Wrath VI', 1317, 6781, 38, 12000, 3000, 1, 0, 90014, 50),
  (108908, 'Book of Mark of the Wild V', 1317, 8908, 40, 14000, 3500, 1, 0, 90014, 51),
  (108935, 'Book of Moonfire VII', 1317, 8935, 40, 14000, 3500, 1, 0, 90014, 52),
  (108911, 'Book of Rejuvenation VII', 1317, 8911, 40, 14000, 3500, 1, 0, 90014, 53),
  (101416, 'Book of Faerie Fire III', 1317, 1416, 42, 16000, 4000, 1, 0, 90014, 54),
  (109751, 'Book of Regrowth VI', 1317, 9751, 42, 16000, 4000, 1, 0, 90014, 55),
  (108954, 'Book of Starfire IV', 1317, 8954, 42, 16000, 4000, 1, 0, 90014, 56),
  (122826, 'Book of Barkskin', 1317, 22826, 44, 18000, 4500, 1, 0, 90014, 57),
  (109759, 'Book of Healing Touch VIII', 1317, 9759, 44, 18000, 4500, 1, 0, 90014, 58),
  (109757, 'Book of Thorns V', 1317, 9757, 44, 18000, 4500, 1, 0, 90014, 59),
  (109836, 'Book of Moonfire VIII', 1317, 9836, 46, 20000, 5000, 1, 0, 90014, 60),
  (109842, 'Book of Rejuvenation VIII', 1317, 9842, 46, 20000, 5000, 1, 0, 90014, 61),
  (108906, 'Book of Wrath VII', 1317, 8906, 46, 20000, 5000, 1, 0, 90014, 62),
  (109854, 'Book of Entangling Roots V', 1317, 9854, 48, 22000, 5500, 1, 0, 90014, 63),
  (109859, 'Book of Regrowth VII', 1317, 9859, 48, 22000, 5500, 1, 0, 90014, 64),
  (109890, 'Book of Healing Touch IX', 1317, 9890, 50, 23000, 5750, 1, 0, 90014, 65),
  (109886, 'Book of Mark of the Wild VI', 1317, 9886, 50, 23000, 5750, 1, 0, 90014, 66),
  (109877, 'Book of Starfire V', 1317, 9877, 50, 23000, 5750, 1, 0, 90014, 67),
  (109837, 'Book of Moonfire IX', 1317, 9837, 52, 26000, 6500, 1, 0, 90014, 68),
  (109843, 'Book of Rejuvenation IX', 1317, 9843, 52, 26000, 6500, 1, 0, 90014, 69),
  (102889, 'Book of Faerie Fire IV', 1317, 2889, 54, 28000, 7000, 1, 0, 90014, 70),
  (109860, 'Book of Regrowth VIII', 1317, 9860, 54, 28000, 7000, 1, 0, 90014, 71),
  (109902, 'Book of Soothe Animal III', 1317, 9902, 54, 28000, 7000, 1, 0, 90014, 72),
  (110343, 'Book of Thorns VI', 1317, 10343, 54, 28000, 7000, 1, 0, 90014, 73),
  (109911, 'Book of Wrath VIII', 1317, 9911, 54, 28000, 7000, 1, 0, 90014, 74),
  (109891, 'Book of Healing Touch X', 1317, 9891, 56, 30000, 7500, 1, 0, 90014, 75),
  (109855, 'Book of Entangling Roots VI', 1317, 9855, 58, 32000, 8000, 1, 0, 90014, 76),
  (118660, 'Book of Hibernate III', 1317, 18660, 58, 32000, 8000, 1, 0, 90014, 77),
  (109838, 'Book of Moonfire X', 1317, 9838, 58, 32000, 8000, 1, 0, 90014, 78),
  (109844, 'Book of Rejuvenation X', 1317, 9844, 58, 32000, 8000, 1, 0, 90014, 79),
  (109878, 'Book of Starfire VI', 1317, 9878, 58, 32000, 8000, 1, 0, 90014, 80),
  (109887, 'Book of Mark of the Wild VII', 1317, 9887, 60, 34000, 8500, 1, 0, 90014, 81),
  (109861, 'Book of Regrowth IX', 1317, 9861, 60, 34000, 8500, 1, 0, 90014, 82),
  (101736, 'Book of Demoralizing Roar I', 1317, 1736, 10, 300, 75, 1, 5487, 90017, 0),
  (106810, 'Book of Maul I', 1317, 6810, 10, 300, 75, 1, 5487, 90017, 1),
  (105228, 'Book of Enrage', 1317, 5228, 12, 800, 200, 1, 5487, 90017, 2),
  (105212, 'Book of Bash I', 1317, 5212, 14, 900, 225, 1, 5487, 90017, 3),
  (103139, 'Book of Swipe I', 1317, 3139, 16, 1800, 450, 1, 5487, 90017, 4),
  (106811, 'Book of Maul II', 1317, 6811, 18, 1900, 475, 1, 5487, 90017, 5),
  (101448, 'Book of Claw I', 1317, 1448, 20, 2000, 500, 1, 768, 90017, 6),
  (101737, 'Book of Demoralizing Roar II', 1317, 1737, 20, 2000, 500, 1, 5487, 90017, 7),
  (105216, 'Book of Prowl I', 1317, 5216, 20, 2000, 500, 1, 768, 90017, 8),
  (101445, 'Book of Rip I', 1317, 1445, 20, 2000, 500, 1, 768, 90017, 9),
  (105222, 'Book of Shred I', 1317, 5222, 22, 3000, 750, 1, 768, 90017, 10),
  (101827, 'Book of Rake I', 1317, 1827, 24, 4000, 1000, 1, 768, 90017, 11),
  (101432, 'Book of Swipe II', 1317, 1432, 24, 4000, 1000, 1, 5487, 90017, 12),
  (105218, 'Book of Tiger''s Fury I', 1317, 5218, 24, 4000, 1000, 1, 768, 90017, 13),
  (101151, 'Book of Dash I', 1317, 1151, 26, 4500, 1125, 1, 768, 90017, 14),
  (106812, 'Book of Maul III', 1317, 6812, 26, 4500, 1125, 1, 5487, 90017, 15),
  (105210, 'Book of Challenging Roar', 1317, 5210, 28, 5000, 1250, 1, 5487, 90017, 16),
  (103030, 'Book of Claw II', 1317, 3030, 28, 5000, 1250, 1, 768, 90017, 17),
  (108999, 'Book of Cower I', 1317, 8999, 28, 5000, 1250, 1, 768, 90017, 18),
  (109494, 'Book of Rip II', 1317, 9494, 28, 5000, 1250, 1, 768, 90017, 19),
  (106799, 'Book of Bash II', 1317, 6799, 30, 6000, 1500, 1, 5487, 90017, 20),
  (106801, 'Book of Shred II', 1317, 6801, 30, 6000, 1500, 1, 768, 90017, 21),
  (109491, 'Book of Demoralizing Roar III', 1317, 9491, 32, 8000, 2000, 1, 5487, 90017, 22),
  (122569, 'Book of Ferocious Bite I', 1317, 22569, 32, 8000, 2000, 1, 768, 90017, 23),
  (106786, 'Book of Ravage I', 1317, 6786, 32, 8000, 2000, 1, 768, 90017, 24),
  (105226, 'Book of Track Humanoids', 1317, 5226, 32, 8000, 2000, 1, 768, 90017, 25),
  (108973, 'Book of Maul IV', 1317, 8973, 34, 10000, 2500, 1, 5487, 90017, 26),
  (101828, 'Book of Rake II', 1317, 1828, 34, 10000, 2500, 1, 768, 90017, 27),
  (101433, 'Book of Swipe III', 1317, 1433, 34, 10000, 2500, 1, 5487, 90017, 28),
  (122894, 'Book of Frenzied Regeneration I', 1317, 22894, 36, 11000, 2750, 1, 5487, 90017, 29),
  (109006, 'Book of Pounce I', 1317, 9006, 36, 11000, 2750, 1, 768, 90017, 30),
  (109495, 'Book of Rip III', 1317, 9495, 36, 11000, 2750, 1, 768, 90017, 31),
  (106794, 'Book of Tiger''s Fury II', 1317, 6794, 36, 11000, 2750, 1, 768, 90017, 32),
  (105203, 'Book of Claw III', 1317, 5203, 38, 12000, 3000, 1, 768, 90017, 33),
  (108993, 'Book of Shred III', 1317, 8993, 38, 12000, 3000, 1, 768, 90017, 34),
  (109001, 'Book of Cower II', 1317, 9001, 40, 14000, 3500, 1, 768, 90017, 35),
  (120722, 'Book of Feline Grace', 1317, 20722, 40, 14000, 3500, 1, 768, 90017, 36),
  (122830, 'Book of Ferocious Bite II', 1317, 22830, 40, 14000, 3500, 1, 768, 90017, 37),
  (106784, 'Book of Prowl II', 1317, 6784, 40, 14000, 3500, 1, 768, 90017, 38),
  (109748, 'Book of Demoralizing Roar IV', 1317, 9748, 42, 16000, 4000, 1, 5487, 90017, 39),
  (109746, 'Book of Maul V', 1317, 9746, 42, 16000, 4000, 1, 5487, 90017, 40),
  (106790, 'Book of Ravage II', 1317, 6790, 42, 16000, 4000, 1, 768, 90017, 41),
  (101829, 'Book of Rake III', 1317, 1829, 44, 18000, 4500, 1, 768, 90017, 42),
  (109753, 'Book of Rip IV', 1317, 9753, 44, 18000, 4500, 1, 768, 90017, 43),
  (109755, 'Book of Swipe IV', 1317, 9755, 44, 18000, 4500, 1, 5487, 90017, 44),
  (108984, 'Book of Bash III', 1317, 8984, 46, 20000, 5000, 1, 5487, 90017, 45),
  (109822, 'Book of Dash II', 1317, 9822, 46, 20000, 5000, 1, 768, 90017, 46),
  (122897, 'Book of Frenzied Regeneration II', 1317, 22897, 46, 20000, 5000, 1, 5487, 90017, 47),
  (109825, 'Book of Pounce II', 1317, 9825, 46, 20000, 5000, 1, 768, 90017, 48),
  (109831, 'Book of Shred IV', 1317, 9831, 46, 20000, 5000, 1, 768, 90017, 49),
  (105204, 'Book of Claw IV', 1317, 5204, 48, 22000, 5500, 1, 768, 90017, 50),
  (122831, 'Book of Ferocious Bite III', 1317, 22831, 48, 22000, 5500, 1, 768, 90017, 51),
  (109847, 'Book of Tiger''s Fury III', 1317, 9847, 48, 22000, 5500, 1, 768, 90017, 52),
  (109882, 'Book of Maul VI', 1317, 9882, 50, 23000, 5750, 1, 5487, 90017, 53),
  (109868, 'Book of Ravage III', 1317, 9868, 50, 23000, 5750, 1, 768, 90017, 54),
  (109893, 'Book of Cower III', 1317, 9893, 52, 26000, 6500, 1, 768, 90017, 55),
  (109899, 'Book of Demoralizing Roar V', 1317, 9899, 52, 26000, 6500, 1, 5487, 90017, 56),
  (109895, 'Book of Rip V', 1317, 9895, 52, 26000, 6500, 1, 768, 90017, 57),
  (109905, 'Book of Rake IV', 1317, 9905, 54, 28000, 7000, 1, 768, 90017, 58),
  (109832, 'Book of Shred V', 1317, 9832, 54, 28000, 7000, 1, 768, 90017, 59),
  (109909, 'Book of Swipe V', 1317, 9909, 54, 28000, 7000, 1, 5487, 90017, 60),
  (122832, 'Book of Ferocious Bite IV', 1317, 22832, 56, 30000, 7500, 1, 768, 90017, 61),
  (122898, 'Book of Frenzied Regeneration III', 1317, 22898, 56, 30000, 7500, 1, 5487, 90017, 62),
  (109828, 'Book of Pounce III', 1317, 9828, 56, 30000, 7500, 1, 768, 90017, 63),
  (109851, 'Book of Claw V', 1317, 9851, 58, 32000, 8000, 1, 768, 90017, 64),
  (109883, 'Book of Maul VII', 1317, 9883, 58, 32000, 8000, 1, 5487, 90017, 65),
  (109869, 'Book of Ravage IV', 1317, 9869, 58, 32000, 8000, 1, 768, 90017, 66),
  (109914, 'Book of Prowl III', 1317, 9914, 60, 34000, 8500, 1, 768, 90017, 67),
  (109897, 'Book of Rip VI', 1317, 9897, 60, 34000, 8500, 1, 768, 90017, 68),
  (109848, 'Book of Tiger''s Fury IV', 1317, 9848, 60, 34000, 8500, 1, 768, 90017, 69);

-- Druid: 12 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (119179, 'Book of Bear Form', 1317, 19179, 10, 0, 0, 3, 0, NULL, NULL),
  (101446, 'Book of Aquatic Form', 1317, 1446, 16, 0, 0, 3, 0, NULL, NULL),
  (100499, 'Book of Cat Form', 1317, 499, 20, 0, 0, 3, 0, NULL, NULL),
  (101441, 'Book of Travel Form', 1317, 1441, 30, 0, 0, 3, 0, NULL, NULL),
  (111594, 'Book of Dire Bear Form', 1317, 11594, 40, 0, 0, 3, 5487, NULL, NULL),
  (129167, 'Book of Innervate', 1317, 29167, 40, 0, 0, 3, 0, NULL, NULL),
  (117397, 'Book of Faerie Fire (Feral) IV', 1317, 17397, 54, 0, 0, 3, 0, NULL, NULL),
  (117376, 'Book of Nature''s Grasp VI', 1317, 17376, 58, 0, 0, 3, 0, NULL, NULL),
  (117406, 'Book of Hurricane III', 1317, 17406, 60, 0, 0, 3, 0, NULL, NULL),
  (124981, 'Book of Insect Swarm V', 1317, 24981, 60, 0, 0, 3, 0, NULL, NULL),
  (120750, 'Book of Rebirth V', 1317, 20750, 60, 0, 0, 3, 0, NULL, NULL),
  (109865, 'Book of Tranquility IV', 1317, 9865, 60, 0, 0, 3, 0, NULL, NULL);

-- Shaman: 138 vendor books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (108020, 'Tablet of Rockbiter Weapon I', 33585, 8020, 1, 10, 2, 1, 0, 90015, 0),
  (108043, 'Tablet of Earth Shock I', 33585, 8043, 4, 100, 25, 1, 0, 90015, 1),
  (101326, 'Tablet of Healing Wave II', 33585, 1326, 6, 100, 25, 1, 0, 90015, 2),
  (108047, 'Tablet of Earth Shock II', 33585, 8047, 8, 100, 25, 1, 0, 90015, 3),
  (101324, 'Tablet of Lightning Bolt II', 33585, 1324, 8, 100, 25, 1, 0, 90015, 4),
  (101303, 'Tablet of Lightning Shield I', 33585, 1303, 8, 100, 25, 1, 0, 90015, 5),
  (108021, 'Tablet of Rockbiter Weapon II', 33585, 8021, 8, 100, 25, 1, 0, 90015, 6),
  (108051, 'Tablet of Flame Shock I', 33585, 8051, 10, 400, 100, 1, 0, 90015, 7),
  (108025, 'Tablet of Flametongue Weapon I', 33585, 8025, 10, 400, 100, 1, 0, 90015, 8),
  (102014, 'Tablet of Ancestral Spirit I', 33585, 2014, 12, 800, 200, 1, 0, 90015, 9),
  (101327, 'Tablet of Healing Wave III', 33585, 1327, 12, 800, 200, 1, 0, 90015, 10),
  (101333, 'Tablet of Purge I', 33585, 1333, 12, 720, 180, 1, 0, 90015, 11),
  (102874, 'Tablet of Cure Disease', 33585, 2874, 14, 900, 225, 1, 0, 90015, 12),
  (108048, 'Tablet of Earth Shock III', 33585, 8048, 14, 900, 225, 1, 0, 90015, 13),
  (101325, 'Tablet of Lightning Bolt III', 33585, 1325, 14, 900, 225, 1, 0, 90015, 14),
  (101315, 'Tablet of Cure Poison', 33585, 1315, 16, 1800, 450, 1, 0, 90015, 15),
  (101304, 'Tablet of Lightning Shield II', 33585, 1304, 16, 1800, 450, 1, 0, 90015, 16),
  (108022, 'Tablet of Rockbiter Weapon III', 33585, 8022, 16, 1800, 450, 1, 0, 90015, 17),
  (108054, 'Tablet of Flame Shock II', 33585, 8054, 18, 2000, 500, 1, 0, 90015, 18),
  (108031, 'Tablet of Flametongue Weapon II', 33585, 8031, 18, 2000, 500, 1, 0, 90015, 19),
  (101354, 'Tablet of Healing Wave IV', 33585, 1354, 18, 2000, 500, 1, 0, 90015, 20),
  (108057, 'Tablet of Frost Shock I', 33585, 8057, 20, 2200, 550, 1, 0, 90015, 21),
  (108035, 'Tablet of Frostbrand Weapon I', 33585, 8035, 20, 2200, 550, 1, 0, 90015, 22),
  (108007, 'Tablet of Lesser Healing Wave I', 33585, 8007, 20, 2200, 550, 1, 0, 90015, 23),
  (101357, 'Tablet of Lightning Bolt IV', 33585, 1357, 20, 2200, 550, 1, 0, 90015, 24),
  (105386, 'Tablet of Water Breathing', 33585, 5386, 22, 3000, 750, 1, 0, 90015, 25),
  (120778, 'Tablet of Ancestral Spirit II', 33585, 20778, 24, 3500, 875, 1, 0, 90015, 26),
  (108049, 'Tablet of Earth Shock IV', 33585, 8049, 24, 3500, 875, 1, 0, 90015, 27),
  (101355, 'Tablet of Healing Wave V', 33585, 1355, 24, 3500, 875, 1, 0, 90015, 28),
  (101305, 'Tablet of Lightning Shield III', 33585, 1305, 24, 3500, 875, 1, 0, 90015, 29),
  (110401, 'Tablet of Rockbiter Weapon IV', 33585, 10401, 24, 3500, 875, 1, 0, 90015, 30),
  (101345, 'Tablet of Far Sight', 33585, 1345, 26, 4000, 1000, 1, 0, 90015, 31),
  (108032, 'Tablet of Flametongue Weapon III', 33585, 8032, 26, 4000, 1000, 1, 0, 90015, 32),
  (101358, 'Tablet of Lightning Bolt V', 33585, 1358, 26, 4000, 1000, 1, 0, 90015, 33),
  (108055, 'Tablet of Flame Shock III', 33585, 8055, 28, 6000, 1500, 1, 0, 90015, 34),
  (108039, 'Tablet of Frostbrand Weapon II', 33585, 8039, 28, 6000, 1500, 1, 0, 90015, 35),
  (108009, 'Tablet of Lesser Healing Wave II', 33585, 8009, 28, 6000, 1500, 1, 0, 90015, 36),
  (101338, 'Tablet of Water Walking', 33585, 1338, 28, 6000, 1500, 1, 0, 90015, 37),
  (101356, 'Tablet of Healing Wave VI', 33585, 1356, 32, 8000, 2000, 1, 0, 90015, 38),
  (106043, 'Tablet of Lightning Bolt VI', 33585, 6043, 32, 8000, 2000, 1, 0, 90015, 39),
  (101363, 'Tablet of Lightning Shield IV', 33585, 1363, 32, 8000, 2000, 1, 0, 90015, 40),
  (108013, 'Tablet of Purge II', 33585, 8013, 32, 7200, 1800, 1, 0, 90015, 41),
  (108059, 'Tablet of Frost Shock II', 33585, 8059, 34, 9000, 2250, 1, 0, 90015, 42),
  (110402, 'Tablet of Rockbiter Weapon V', 33585, 10402, 34, 9000, 2250, 1, 0, 90015, 43),
  (120779, 'Tablet of Ancestral Spirit III', 33585, 20779, 36, 10000, 2500, 1, 0, 90015, 44),
  (110415, 'Tablet of Earth Shock V', 33585, 10415, 36, 10000, 2500, 1, 0, 90015, 45),
  (110446, 'Tablet of Flametongue Weapon IV', 33585, 10446, 36, 10000, 2500, 1, 0, 90015, 46),
  (108011, 'Tablet of Lesser Healing Wave III', 33585, 8011, 36, 10000, 2500, 1, 0, 90015, 47),
  (110457, 'Tablet of Frostbrand Weapon III', 33585, 10457, 38, 11000, 2750, 1, 0, 90015, 48),
  (110393, 'Tablet of Lightning Bolt VII', 33585, 10393, 38, 11000, 2750, 1, 0, 90015, 49),
  (110449, 'Tablet of Flame Shock IV', 33585, 10449, 40, 12000, 3000, 1, 0, 90015, 50),
  (108006, 'Tablet of Healing Wave VII', 33585, 8006, 40, 12000, 3000, 1, 0, 90015, 51),
  (108135, 'Tablet of Lightning Shield V', 33585, 8135, 40, 12000, 3000, 1, 0, 90015, 52),
  (110469, 'Tablet of Lesser Healing Wave IV', 33585, 10469, 44, 18000, 4500, 1, 0, 90015, 53),
  (110394, 'Tablet of Lightning Bolt VIII', 33585, 10394, 44, 18000, 4500, 1, 0, 90015, 54),
  (116317, 'Tablet of Rockbiter Weapon VI', 33585, 16317, 44, 18000, 4500, 1, 0, 90015, 55),
  (116347, 'Tablet of Flametongue Weapon V', 33585, 16347, 46, 20000, 5000, 1, 0, 90015, 56),
  (110474, 'Tablet of Frost Shock III', 33585, 10474, 46, 20000, 5000, 1, 0, 90015, 57),
  (120780, 'Tablet of Ancestral Spirit IV', 33585, 20780, 48, 22000, 5500, 1, 0, 90015, 58),
  (110416, 'Tablet of Earth Shock VI', 33585, 10416, 48, 22000, 5500, 1, 0, 90015, 59),
  (116357, 'Tablet of Frostbrand Weapon IV', 33585, 16357, 48, 22000, 5500, 1, 0, 90015, 60),
  (110397, 'Tablet of Healing Wave VIII', 33585, 10397, 48, 22000, 5500, 1, 0, 90015, 61),
  (110433, 'Tablet of Lightning Shield VI', 33585, 10433, 48, 22000, 5500, 1, 0, 90015, 62),
  (115209, 'Tablet of Lightning Bolt IX', 33585, 15209, 50, 24000, 6000, 1, 0, 90015, 63),
  (110450, 'Tablet of Flame Shock V', 33585, 10450, 52, 27000, 6750, 1, 0, 90015, 64),
  (110470, 'Tablet of Lesser Healing Wave V', 33585, 10470, 52, 27000, 6750, 1, 0, 90015, 65),
  (116318, 'Tablet of Rockbiter Weapon VII', 33585, 16318, 54, 29000, 7250, 1, 0, 90015, 66),
  (116348, 'Tablet of Flametongue Weapon VI', 33585, 16348, 56, 30000, 7500, 1, 0, 90015, 67),
  (110398, 'Tablet of Healing Wave IX', 33585, 10398, 56, 30000, 7500, 1, 0, 90015, 68),
  (115210, 'Tablet of Lightning Bolt X', 33585, 15210, 56, 30000, 7500, 1, 0, 90015, 69),
  (110434, 'Tablet of Lightning Shield VII', 33585, 10434, 56, 30000, 7500, 1, 0, 90015, 70),
  (110475, 'Tablet of Frost Shock IV', 33585, 10475, 58, 32000, 8000, 1, 0, 90015, 71),
  (116358, 'Tablet of Frostbrand Weapon V', 33585, 16358, 58, 32000, 8000, 1, 0, 90015, 72),
  (120781, 'Tablet of Ancestral Spirit V', 33585, 20781, 60, 34000, 8500, 1, 0, 90015, 73),
  (110417, 'Tablet of Earth Shock VII', 33585, 10417, 60, 34000, 8500, 1, 0, 90015, 74),
  (110471, 'Tablet of Lesser Healing Wave VI', 33585, 10471, 60, 34000, 8500, 1, 0, 90015, 75),
  (102076, 'Tablet of Earthbind Totem', 33585, 2076, 6, 100, 25, 1, 0, 90016, 0),
  (105731, 'Tablet of Stoneclaw Totem I', 33585, 5731, 8, 100, 25, 1, 0, 90016, 1),
  (108077, 'Tablet of Strength of Earth Totem I', 33585, 8077, 10, 400, 100, 1, 0, 90016, 2),
  (108086, 'Tablet of Fire Nova Totem I', 33585, 8086, 12, 800, 200, 1, 0, 90016, 3),
  (108158, 'Tablet of Stoneskin Totem II', 33585, 8158, 14, 900, 225, 1, 0, 90016, 4),
  (106400, 'Tablet of Stoneclaw Totem II', 33585, 6400, 18, 2000, 500, 1, 0, 90016, 5),
  (108144, 'Tablet of Tremor Totem', 33585, 8144, 18, 2000, 500, 1, 0, 90016, 6),
  (106379, 'Tablet of Searing Totem II', 33585, 6379, 20, 2200, 550, 1, 0, 90016, 7),
  (108500, 'Tablet of Fire Nova Totem II', 33585, 8500, 22, 3000, 750, 1, 0, 90016, 8),
  (108169, 'Tablet of Poison Cleansing Totem', 33585, 8169, 22, 3000, 750, 1, 0, 90016, 9),
  (108183, 'Tablet of Frost Resistance Totem I', 33585, 8183, 24, 3500, 875, 1, 0, 90016, 10),
  (108159, 'Tablet of Stoneskin Totem III', 33585, 8159, 24, 3500, 875, 1, 0, 90016, 11),
  (108164, 'Tablet of Strength of Earth Totem II', 33585, 8164, 24, 3500, 875, 1, 0, 90016, 12),
  (108189, 'Tablet of Magma Totem I', 33585, 8189, 26, 4000, 1000, 1, 0, 90016, 13),
  (105678, 'Tablet of Mana Spring Totem I', 33585, 5678, 26, 4000, 1000, 1, 0, 90016, 14),
  (108186, 'Tablet of Fire Resistance Totem I', 33585, 8186, 28, 6000, 1500, 1, 0, 90016, 15),
  (108231, 'Tablet of Flametongue Totem I', 33585, 8231, 28, 6000, 1500, 1, 0, 90016, 16),
  (106401, 'Tablet of Stoneclaw Totem III', 33585, 6401, 28, 6000, 1500, 1, 0, 90016, 17),
  (108180, 'Tablet of Grounding Totem', 33585, 8180, 30, 7000, 1750, 1, 0, 90016, 18),
  (106383, 'Tablet of Healing Stream Totem II', 33585, 6383, 30, 7000, 1750, 1, 0, 90016, 19),
  (110597, 'Tablet of Nature Resistance Totem I', 33585, 10597, 30, 7000, 1750, 1, 0, 90016, 20),
  (106380, 'Tablet of Searing Totem III', 33585, 6380, 30, 7000, 1750, 1, 0, 90016, 21),
  (108501, 'Tablet of Fire Nova Totem III', 33585, 8501, 32, 8000, 2000, 1, 0, 90016, 22),
  (106496, 'Tablet of Sentry Totem', 33585, 6496, 34, 9000, 2250, 1, 0, 90016, 23),
  (110409, 'Tablet of Stoneskin Totem IV', 33585, 10409, 34, 9000, 2250, 1, 0, 90016, 24),
  (110588, 'Tablet of Magma Totem II', 33585, 10588, 36, 10000, 2500, 1, 0, 90016, 25),
  (110512, 'Tablet of Mana Spring Totem II', 33585, 10512, 36, 10000, 2500, 1, 0, 90016, 26),
  (115113, 'Tablet of Windwall Totem I', 33585, 15113, 36, 10000, 2500, 1, 0, 90016, 27),
  (108173, 'Tablet of Disease Cleansing Totem', 33585, 8173, 38, 11000, 2750, 1, 0, 90016, 28),
  (108252, 'Tablet of Flametongue Totem II', 33585, 8252, 38, 11000, 2750, 1, 0, 90016, 29),
  (110480, 'Tablet of Frost Resistance Totem II', 33585, 10480, 38, 11000, 2750, 1, 0, 90016, 30),
  (106402, 'Tablet of Stoneclaw Totem IV', 33585, 6402, 38, 11000, 2750, 1, 0, 90016, 31),
  (108165, 'Tablet of Strength of Earth Totem III', 33585, 8165, 38, 11000, 2750, 1, 0, 90016, 32),
  (106384, 'Tablet of Healing Stream Totem III', 33585, 6384, 40, 12000, 3000, 1, 0, 90016, 33),
  (106381, 'Tablet of Searing Totem IV', 33585, 6381, 40, 12000, 3000, 1, 0, 90016, 34),
  (111316, 'Tablet of Fire Nova Totem IV', 33585, 11316, 42, 16000, 4000, 1, 0, 90016, 35),
  (110540, 'Tablet of Fire Resistance Totem II', 33585, 10540, 42, 16000, 4000, 1, 0, 90016, 36),
  (108837, 'Tablet of Grace of Air Totem I', 33585, 8837, 42, 16000, 4000, 1, 0, 90016, 37),
  (110602, 'Tablet of Nature Resistance Totem II', 33585, 10602, 44, 18000, 4500, 1, 0, 90016, 38),
  (110410, 'Tablet of Stoneskin Totem V', 33585, 10410, 44, 18000, 4500, 1, 0, 90016, 39),
  (110589, 'Tablet of Magma Totem III', 33585, 10589, 46, 20000, 5000, 1, 0, 90016, 40),
  (110514, 'Tablet of Mana Spring Totem III', 33585, 10514, 46, 20000, 5000, 1, 0, 90016, 41),
  (115115, 'Tablet of Windwall Totem II', 33585, 15115, 46, 20000, 5000, 1, 0, 90016, 42),
  (110528, 'Tablet of Flametongue Totem III', 33585, 10528, 48, 22000, 5500, 1, 0, 90016, 43),
  (110429, 'Tablet of Stoneclaw Totem V', 33585, 10429, 48, 22000, 5500, 1, 0, 90016, 44),
  (110464, 'Tablet of Healing Stream Totem IV', 33585, 10464, 50, 24000, 6000, 1, 0, 90016, 45),
  (110439, 'Tablet of Searing Totem V', 33585, 10439, 50, 24000, 6000, 1, 0, 90016, 46),
  (125910, 'Tablet of Tranquil Air Totem', 33585, 25910, 50, 24000, 6000, 1, 0, 90016, 47),
  (111317, 'Tablet of Fire Nova Totem V', 33585, 11317, 52, 27000, 6750, 1, 0, 90016, 48),
  (110443, 'Tablet of Strength of Earth Totem IV', 33585, 10443, 52, 27000, 6750, 1, 0, 90016, 49),
  (110481, 'Tablet of Frost Resistance Totem III', 33585, 10481, 54, 29000, 7250, 1, 0, 90016, 50),
  (110411, 'Tablet of Stoneskin Totem VI', 33585, 10411, 54, 29000, 7250, 1, 0, 90016, 51),
  (110628, 'Tablet of Grace of Air Totem II', 33585, 10628, 56, 30000, 7500, 1, 0, 90016, 52),
  (110590, 'Tablet of Magma Totem IV', 33585, 10590, 56, 30000, 7500, 1, 0, 90016, 53),
  (110515, 'Tablet of Mana Spring Totem IV', 33585, 10515, 56, 30000, 7500, 1, 0, 90016, 54),
  (115116, 'Tablet of Windwall Totem III', 33585, 15116, 56, 30000, 7500, 1, 0, 90016, 55),
  (110541, 'Tablet of Fire Resistance Totem III', 33585, 10541, 58, 32000, 8000, 1, 0, 90016, 56),
  (116394, 'Tablet of Flametongue Totem IV', 33585, 16394, 58, 32000, 8000, 1, 0, 90016, 57),
  (110430, 'Tablet of Stoneclaw Totem VI', 33585, 10430, 58, 32000, 8000, 1, 0, 90016, 58),
  (110465, 'Tablet of Healing Stream Totem V', 33585, 10465, 60, 34000, 8500, 1, 0, 90016, 59),
  (110603, 'Tablet of Nature Resistance Totem III', 33585, 10603, 60, 34000, 8500, 1, 0, 90016, 60),
  (110440, 'Tablet of Searing Totem VI', 33585, 10440, 60, 34000, 8500, 1, 0, 90016, 61);

-- Shaman: 8 capstone books
INSERT INTO sbk_def (entry, name, displayid, teach_spell, req_level, buy_price, sell_price, quality, required_spell, vendor, slot) VALUES
  (105387, 'Tablet of Ghost Wolf', 33585, 5387, 20, 0, 0, 3, 0, NULL, NULL),
  (101352, 'Tablet of Astral Recall', 33585, 1352, 30, 0, 0, 3, 0, NULL, NULL),
  (120613, 'Tablet of Reincarnation', 33585, 20613, 30, 0, 0, 3, 0, NULL, NULL),
  (110616, 'Tablet of Windfury Totem III', 33585, 10616, 52, 0, 0, 3, 0, NULL, NULL),
  (110625, 'Tablet of Chain Heal III', 33585, 10625, 54, 0, 0, 3, 0, NULL, NULL),
  (102863, 'Tablet of Chain Lightning IV', 33585, 2863, 56, 0, 0, 3, 0, NULL, NULL),
  (117363, 'Tablet of Mana Tide Totem III', 33585, 17363, 58, 0, 0, 3, 0, NULL, NULL),
  (116363, 'Tablet of Windfury Weapon IV', 33585, 16363, 60, 0, 0, 3, 0, NULL, NULL);

-- -----------------------------------------------------------------------------
-- 4. Build the items. Every book is a clone of the stock "Tome of Fireball"
--    (8803) with its fields replaced, so the script keeps working if upstream
--    adds columns to item_template.
--      Flags 64             usable (drops the stock "deprecated" flag)
--      AllowableClass 1490  the six caster classes
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS sbk_item;
CREATE TEMPORARY TABLE sbk_item AS
SELECT i.*, d.entry AS sbk_new_entry
FROM item_template i
CROSS JOIN sbk_def d
WHERE i.entry = 8803;

UPDATE sbk_item s
JOIN sbk_def d ON d.entry = s.sbk_new_entry
SET s.entry          = d.entry,
    s.name           = d.name,
    s.displayid      = d.displayid,
    s.Quality        = d.quality,
    s.Flags          = 64,
    s.BuyPrice       = d.buy_price,
    s.SellPrice      = d.sell_price,
    s.AllowableClass = 1490,
    s.ItemLevel      = GREATEST(d.req_level, 1),
    s.RequiredLevel  = d.req_level,
    s.requiredspell  = d.required_spell,
    s.spellid_1      = d.teach_spell;

ALTER TABLE sbk_item DROP COLUMN sbk_new_entry;

REPLACE INTO item_template SELECT * FROM sbk_item;

DROP TEMPORARY TABLE IF EXISTS sbk_item;

-- -----------------------------------------------------------------------------
-- 5. Vendor stock, rebuilt from the definitions each run so a book that
--    changes tier leaves or joins a vendor cleanly. A vendor shows at most
--    128 items; the largest list here is 125.
-- -----------------------------------------------------------------------------
DELETE FROM npc_vendor WHERE entry BETWEEN 90010 AND 90017;

INSERT INTO npc_vendor (entry, item, maxcount, incrtime, slot, condition_id, comments)
SELECT d.vendor, d.entry, 0, 0, d.slot, @sbk_cond, d.name
FROM sbk_def d
WHERE d.vendor IS NOT NULL
ORDER BY d.vendor, d.slot;

DROP TEMPORARY TABLE IF EXISTS sbk_def;

-- -----------------------------------------------------------------------------
-- 6. Totem relics become shaman-only on the World Shaman Trainer. Uses the
--    stock "player is a Shaman" condition (class mask 64); skipped if that
--    condition is missing.
-- -----------------------------------------------------------------------------
SET @sbk_shaman := (
  SELECT condition_entry FROM conditions
  WHERE type = 14 AND value1 = 0 AND value2 = 64 AND value3 = 0 AND value4 = 0 AND flags = 0
    AND condition_entry < 65536
  LIMIT 1
);

UPDATE npc_vendor
SET condition_id = @sbk_shaman
WHERE entry = 4991
  AND item IN (5175, 5176, 5177, 5178)
  AND @sbk_shaman IS NOT NULL;
