-- =============================================================================
-- spellbook_vendor.sql
-- Prototype for cross-class spell books on CMaNGOS Classic (1.12.1):
-- one neutral vendor selling four test books to the caster classes.
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/spellbook_vendor.sql
-- Spawn:   .npc add 90000   (GM account, standing where you want him)
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- Custom ids (all unused in stock data):
--   creature 90000  Arcane Bookseller
--   item     90001  Tome of Fireball II      -> teaches Fireball (Rank 2)
--   item     90002  Grimoire of Summon Imp   -> teaches Summon Imp
--   item     90003  Book of Bear Form        -> teaches Bear Form and Growl
--   item     90004  Book of Maul             -> teaches Maul (Rank 1)
--   condition 60001+ "player is a caster class" (gates the vendor stock;
--                    the script picks the first free id from 60001 up)
--
-- Class restriction: mask 1490 = Paladin(2) + Priest(16) + Shaman(64) +
-- Mage(128) + Warlock(256) + Druid(1024). Warriors and Rogues have no mana;
-- Hunters cannot summon demons. Two layers enforce it:
--   * the vendor does not list or sell the books to other classes
--   * the books themselves cannot be used by other classes
-- A character in GM mode (.gm on) bypasses the vendor filter, so test the
-- restriction with .gm off. To let Hunters buy the Fireball tome, give that
-- one book mask 1494 and its own condition.
--
-- How a book works: it is a consumable whose on-use spell is a stock "teach"
-- spell. Every row here is cloned from a stock row and then edited, so the
-- script keeps working if upstream adds columns to these tables.
--
-- Safe to run on every startup: the three custom rows are rewritten in place
-- (REPLACE), nothing stock is modified.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Books. Cloned from the stock "Tome of Fireball" (8803), which is
--    mage-only and flagged deprecated.
--      Flags 64          usable, without the stock "deprecated" red-icon flag
--      AllowableClass 1490  caster classes only (see header)
--      RequiredLevel 1   FOR TESTING. Fireball Rank 2 is a level 6 spell.
--      BuyPrice 0        FOR TESTING. New characters start with no money.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS sb_item;
CREATE TEMPORARY TABLE sb_item AS
SELECT * FROM item_template WHERE entry = 8803;

-- There is no stock teach spell for Fireball Rank 1 (mages are created with
-- it), so the lowest rank a book can teach is Rank 2, via teach spell 483.
UPDATE sb_item
SET entry          = 90001,
    name           = 'Tome of Fireball II',
    Flags          = 64,
    AllowableClass = 1490,
    RequiredLevel  = 1,
    spellid_1      = 483,     -- teaches Fireball Rank 2 (143)
    BuyPrice       = 0,       -- free FOR TESTING
    SellPrice      = 0;

REPLACE INTO item_template SELECT * FROM sb_item;

UPDATE sb_item
SET entry          = 90002,
    name           = 'Grimoire of Summon Imp',
    displayid      = 1246,    -- stock warlock grimoire icon
    spellid_1      = 7763,    -- "Teach Summon Imp": teaches Summon Imp (688)
    BuyPrice       = 0,       -- free FOR TESTING
    SellPrice      = 0;

REPLACE INTO item_template SELECT * FROM sb_item;

-- Shapeshift test. Bear Form is normally a level 10 druid quest reward; the
-- stock teach spell grants Growl with it. Maul is a rage ability usable only
-- in Bear Form, so the pair tests both the form and a form-bound ability.
UPDATE sb_item
SET entry          = 90003,
    name           = 'Book of Bear Form',
    displayid      = 1317,    -- stock druid book icon
    spellid_1      = 19179;   -- teaches Bear Form (5487) and Growl (6795)

REPLACE INTO item_template SELECT * FROM sb_item;

UPDATE sb_item
SET entry          = 90004,
    name           = 'Book of Maul',
    displayid      = 1317,
    spellid_1      = 6810;    -- teaches Maul Rank 1 (6807)

REPLACE INTO item_template SELECT * FROM sb_item;

DROP TEMPORARY TABLE IF EXISTS sb_item;

-- -----------------------------------------------------------------------------
-- 2. Vendor. Cloned from Cowardly Crosby (2672), a stock level 40 vendor who
--    is already neutral to both factions (faction 35).
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS sb_npc;
CREATE TEMPORARY TABLE sb_npc AS
SELECT * FROM creature_template WHERE Entry = 2672;

UPDATE sb_npc
SET Entry   = 90000,
    Name    = 'Arcane Bookseller',
    SubName = 'Spell Books';

REPLACE INTO creature_template SELECT * FROM sb_npc;

DROP TEMPORARY TABLE IF EXISTS sb_npc;

-- -----------------------------------------------------------------------------
-- 3. Condition: player's class is in mask 1490. Type 14 checks race mask
--    (value1, 0 = any) and class mask (value2), as stock conditions do.
--
--    The id MUST be below 65536: the server reads a vendor row's condition
--    id as a 16-bit number, so a larger id is silently truncated and the row
--    is thrown away at startup. (An earlier version used 900001, which the
--    server read as 48033.) Stock data uses ids up to about 20500, so this
--    takes the first free id from 60001 up and reuses it on later runs.
-- -----------------------------------------------------------------------------
DELETE FROM conditions WHERE condition_entry = 900001;   -- the earlier, too-large id

SET @sb_cond := (
  SELECT condition_entry FROM conditions
  WHERE type = 14 AND value1 = 0 AND value2 = 1490 AND value3 = 0 AND value4 = 0 AND flags = 0
    AND condition_entry < 65536
  LIMIT 1
);

SET @sb_cond := IFNULL(@sb_cond, (
  SELECT IFNULL(MAX(condition_entry) + 1, 60001) FROM conditions
  WHERE condition_entry BETWEEN 60001 AND 65534
));

INSERT IGNORE INTO conditions (condition_entry, type, value1, value2, value3, value4, flags, comments) VALUES
  (@sb_cond, 14, 0, 1490, 0, 0, 0, 'Player ClassMask: 1490 (spell book casters)');

-- -----------------------------------------------------------------------------
-- 4. Stock. maxcount 0 / incrtime 0 = unlimited. condition_id hides the row
--    from, and refuses the sale to, any class outside the mask.
-- -----------------------------------------------------------------------------
REPLACE INTO npc_vendor (entry, item, maxcount, incrtime, slot, condition_id, comments) VALUES
  (90000, 90001, 0, 0, 0, @sb_cond, 'Tome of Fireball II'),
  (90000, 90002, 0, 0, 1, @sb_cond, 'Grimoire of Summon Imp'),
  (90000, 90003, 0, 0, 2, @sb_cond, 'Book of Bear Form'),
  (90000, 90004, 0, 0, 3, @sb_cond, 'Book of Maul');
