-- =============================================================================
-- cooked_food_buffs.sql
-- Every cooked food gives a stat buff, and food buffs last an hour, on
-- CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/cooked_food_buffs.sql, then restart.
--          Item and spell data load only at server start.
-- Built against: classic-db ec4f596 (2026-09-22) with all updates applied.
--
-- Background
--   Cooking makes 78 foods. 44 already give Well Fed, 10 give a buff under
--   another name, and 5 are novelty items. The other 19 only heal. Each of
--   those uses a generic eating spell, and the game already has a twin of
--   that spell which heals the same and also gives a buff. This script points
--   the 19 foods at the twin.
--
--   Because the twins are stock spells, the food's own "Use:" tooltip changes
--   to describe the buff with no client patch.
--
-- The swaps
--   16 foods get an exact twin: same level tier, same healing, same eating
--   time, plus Well Fed (Stamina and Spirit, 15 minutes).
--   3 foods have no exact twin and are judgment calls, marked below.
--
-- Food buff duration (section 3)
--   Stock food buffs last 10 or 15 minutes. This raises them to an hour:
--   every spell named Well Fed, plus the seven stat and regeneration buffs
--   that cooked foods give under other names. It is separate from
--   hour_long_buffs.sql, which handles class buffs: the two scripts keep
--   their own lists and their own records, and neither touches the other's
--   spells. Do not add Well Fed to that script's list.
--
-- TUNING
--   @apply (section 2): 1 gives the 19 foods their buffs, 0 puts them back.
--   @food_buff_index (section 3): how long food buffs last.
--       42 = 1 hour, 367 = 2 hours, 30 = 30 minutes, 0 = stock durations.
--   The two settings are independent.
--   To leave one food alone, put -- at the start of its line. The last line
--   of a list ends with a semicolon: if you drop that one, change the comma
--   on the new last line to a semicolon.
--
-- Safe to run on every startup, and self-limiting: a food is changed only
-- while it still has the spell this script expects, so an upstream change to
-- a food is never overwritten.
-- =============================================================================

SET @apply := 1;
SET @food_buff_index := 42;   -- 1 hour

-- -----------------------------------------------------------------------------
-- 1. The foods: item, the plain eating spell it has in stock data, and the
--    buffed twin it should use instead.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS cfb_food;
CREATE TEMPORARY TABLE cfb_food (
  item        MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  plain_spell MEDIUMINT UNSIGNED NOT NULL,
  buff_spell  MEDIUMINT UNSIGNED NOT NULL
);

INSERT INTO cfb_food (item, plain_spell, buff_spell) VALUES
  -- Level 1 tier: Well Fed, +2 Stamina and Spirit
  (787,   433,  5004),    -- Slitherskin Mackerel
  (6290,  433,  5004),    -- Brilliant Smallfish
  -- Level 5 tier: Well Fed, +4 Stamina and Spirit
  (4592,  434,  5005),    -- Longjaw Mud Snapper
  (5095,  434,  5005),    -- Rainbow Fin Albacore
  (6316,  434,  5005),    -- Loch Frenzy Delight
  (6890,  434,  5005),    -- Smoked Bear Meat
  -- Level 5 to 15 tier: Well Fed, +6 Stamina and Spirit
  (733,   435,  5006),    -- Westfall Stew
  (2685,  435,  5006),    -- Succulent Pork Ribs
  (5478,  435,  5006),    -- Dig Rat Stew
  (5526,  435,  5006),    -- Clam Chowder
  (4593,  435,  5006),    -- Bristle Whisker Catfish
  -- Level 25 tier: Well Fed, +8 Stamina and Spirit
  (4594,  1127, 5007),    -- Rockscale Cod
  (8364,  1127, 5007),    -- Mithril Head Trout
  -- Level 35 tier: Well Fed, +12 Stamina and Spirit
  (6887,  1129, 10256),   -- Spotted Yellowtail
  (13930, 1129, 10256),   -- Filet of Redgill
  (16766, 1129, 10256),   -- Undermine Clam Chowder
  -- JUDGMENT CALLS: no exact twin exists for these three.
  -- Cooked Crab Claw heals 70 a tick; the nearest twin heals 58 and adds
  -- Well Fed +4. It loses a little healing to gain the buff.
  (2682,  2639, 5005),    -- Cooked Crab Claw
  -- The level 45 tier has no Well Fed twin. This one heals the same and gives
  -- +10 Stamina for 10 minutes, applied at once and not after 10 seconds.
  (13933, 1131, 18234),   -- Lobster Stew
  (13935, 1131, 18234);   -- Baked Salmon

-- -----------------------------------------------------------------------------
-- 2. Apply or undo. Only the eating spell changes; the item's cooldown,
--    charges and everything else stay as they are.
-- -----------------------------------------------------------------------------
UPDATE item_template i
JOIN cfb_food f ON f.item = i.entry
SET i.spellid_1 = IF(@apply = 1, f.buff_spell, f.plain_spell)
WHERE i.spellid_1 = IF(@apply = 1, f.plain_spell, f.buff_spell);

DROP TEMPORARY TABLE IF EXISTS cfb_food;

-- -----------------------------------------------------------------------------
-- 3. Food buff duration.
--    Durations are an index into a fixed list in the client data; this moves
--    each food buff from its stock entry (6 = 10 minutes, 347 = 15 minutes)
--    to the entry chosen in @food_buff_index.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS cfb_buff;
CREATE TEMPORARY TABLE cfb_buff (
  spell MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY
);

-- Every Well Fed, from any food (ten spells in stock data, all 15 minutes).
INSERT INTO cfb_buff (spell)
SELECT Id FROM spell_template WHERE SpellName = 'Well Fed';

-- Food buffs that go by other names.
INSERT IGNORE INTO cfb_buff (spell) VALUES
  (18191),   -- Increased Stamina +10     10 min   Cooked Glossy Mightfish, Lobster Stew, Baked Salmon
  (18192),   -- Increased Agility +10     10 min   Grilled Squid
  (18193),   -- Increased Spirit +10      10 min   Hot Smoked Bass
  (22730),   -- Increased Intellect +10   10 min   Runn Tum Tuber Surprise
  (18222),   -- Health Regeneration       10 min   Poached Sunscale Salmon
  (18194),   -- Mana Regeneration         10 min   Nightfin Soup
  (25661);   -- Increased Stamina +25     15 min   Dirge's Kickin' Chimaerok Chops

-- Remember each buff's stock duration the first time it is seen. This table
-- lives in the world database, so a world rebuild resets it with everything
-- else.
CREATE TABLE IF NOT EXISTS custom_food_buff_stock (
  Id            INT UNSIGNED NOT NULL,
  DurationIndex INT UNSIGNED NOT NULL,
  PRIMARY KEY (Id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COMMENT='Stock food buff durations (cooked_food_buffs.sql)';

INSERT IGNORE INTO custom_food_buff_stock (Id, DurationIndex)
SELECT s.Id, s.DurationIndex
FROM spell_template s
JOIN cfb_buff b ON b.spell = s.Id
WHERE s.DurationIndex IN (6, 347);            -- still at a stock duration

-- Put everything this section has ever changed back to stock, so a buff
-- removed from the list above returns to normal.
UPDATE spell_template s
JOIN custom_food_buff_stock k ON k.Id = s.Id
SET s.DurationIndex = k.DurationIndex;

-- Raise the buffs on the list.
UPDATE spell_template s
JOIN custom_food_buff_stock k ON k.Id = s.Id
JOIN cfb_buff b ON b.spell = s.Id
SET s.DurationIndex = @food_buff_index
WHERE @food_buff_index <> 0;

DROP TEMPORARY TABLE IF EXISTS cfb_buff;
