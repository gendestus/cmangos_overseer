-- =============================================================================
-- hour_long_buffs.sql
-- Makes the class buffs players keep up on themselves and each other last one
-- hour on CMaNGOS Classic (1.12.1), including the paladin blessings.
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/hour_long_buffs.sql, then restart.
--          Spell data loads only at server start.
-- Built against: mangos-classic 8ec338a, classic-db with all updates applied.
--
-- How a duration is stored
--   Each spell row has a DurationIndex that points into a fixed list of
--   durations in the client data. This script moves a buff from its stock
--   entry to the one-hour entry. Entries used here, confirmed against stock
--   spells:
--       4 = 2 minutes     5 = 5 minutes     6 = 10 minutes   347 = 15 minutes
--      40 = 20 minutes   30 = 30 minutes   42 = 1 hour       367 = 2 hours
--
-- What is changed
--   Every spell whose name is on the list in section 1, in every rank, when it
--     * applies a buff (an aura), and is not a passive,
--     * targets only the caster or friends, and
--     * normally lasts 2, 5, 10, 15, 20 or 30 minutes.
--   The last two rules keep look-alikes out: for example the weakening effect
--   Touch of Weakness puts on an attacker shares the buff's name but targets
--   an enemy, and is left alone.
--
-- What is not changed
--   * Short combat effects: shields, wards, seals, Blessing of Protection,
--     Freedom and Sacrifice. They are not on the list.
--   * Buffs already an hour or longer (Arcane Brilliance, Prayer of Fortitude,
--     Gift of the Wild), and auras, aspects and stances, which never expire.
--   * Shaman weapon imbues and rogue poisons. Those are weapon enchantments
--     with their duration stored elsewhere.
--   * Tooltips. The client's "lasts 5 minutes" text comes from its own data.
--
-- TUNING
--   @target_index below: 42 for one hour, 367 for two hours, 0 to put every
--   buff back to stock. Add or remove names in section 1 freely: each run
--   first restores everything this script has ever changed, then applies the
--   current list. Restart after any change.
--
-- Safe to run on every startup.
-- =============================================================================

SET @target_index := 42;   -- 1 hour

-- -----------------------------------------------------------------------------
-- 1. The buffs. Names match every rank. The comment is the stock duration.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS hlb_buff;
CREATE TEMPORARY TABLE hlb_buff (
  name VARCHAR(64) NOT NULL PRIMARY KEY
);

INSERT INTO hlb_buff (name) VALUES
  -- Druid
  ('Mark of the Wild'),                -- 30 min
  ('Thorns'),                          -- 10 min
  ('Omen of Clarity'),                 -- 10 min
  -- Hunter
  ('Trueshot Aura'),                   -- 30 min
  -- Mage
  ('Arcane Intellect'),                -- 30 min
  ('Frost Armor'),                     -- 30 min
  ('Ice Armor'),                       -- 30 min
  ('Mage Armor'),                      -- 30 min
  ('Amplify Magic'),                   -- 10 min
  ('Dampen Magic'),                    -- 10 min
  -- Paladin
  ('Blessing of Might'),               -- 5 min
  ('Blessing of Wisdom'),              -- 5 min
  ('Blessing of Kings'),               -- 5 min
  ('Blessing of Salvation'),           -- 5 min
  ('Blessing of Light'),               -- 5 min
  ('Blessing of Sanctuary'),           -- 5 min
  ('Greater Blessing of Might'),       -- 15 min
  ('Greater Blessing of Wisdom'),      -- 15 min
  ('Greater Blessing of Kings'),       -- 15 min
  ('Greater Blessing of Salvation'),   -- 15 min
  ('Greater Blessing of Light'),       -- 15 min
  ('Greater Blessing of Sanctuary'),   -- 15 min
  ('Righteous Fury'),                  -- 30 min
  -- Priest
  ('Power Word: Fortitude'),           -- 30 min
  ('Divine Spirit'),                   -- 30 min
  ('Shadow Protection'),               -- 10 min
  ('Prayer of Shadow Protection'),     -- 20 min
  ('Inner Fire'),                      -- 10 min
  ('Fear Ward'),                       -- 10 min
  ('Shadowguard'),                     -- 10 min
  ('Touch of Weakness'),               -- 10 min
  --('Levitate'),                        -- 2 min
  -- Shaman
  ('Lightning Shield'),                -- 10 min
  ('Water Breathing'),                 -- 10 min
  ('Water Walking'),                   -- 10 min
  -- Warlock
  ('Demon Skin'),                      -- 30 min
  ('Demon Armor'),                     -- 30 min
  ('Detect Lesser Invisibility'),      -- 10 min
  ('Detect Invisibility'),             -- 10 min
  ('Detect Greater Invisibility'),     -- 10 min
  ('Unending Breath'),                 -- 10 min
  -- Warrior
  --('Battle Shout');                    -- 2 min

-- -----------------------------------------------------------------------------
-- 2. Remember each spell's stock duration the first time it qualifies.
--    This table lives in the world database, so a world rebuild resets it
--    along with everything else.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS custom_buff_duration_stock (
  Id            INT UNSIGNED NOT NULL,
  DurationIndex INT UNSIGNED NOT NULL,
  PRIMARY KEY (Id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COMMENT='Stock buff durations (hour_long_buffs.sql)';

INSERT IGNORE INTO custom_buff_duration_stock (Id, DurationIndex)
SELECT s.Id, s.DurationIndex
FROM spell_template s
JOIN hlb_buff b ON b.name = s.SpellName
WHERE s.DurationIndex IN (4, 5, 6, 347, 40, 30)                        -- 2 to 30 minutes
  AND (s.Attributes & 64) = 0                                          -- not passive
  AND (s.Effect1 IN (6, 35) OR s.Effect2 IN (6, 35) OR s.Effect3 IN (6, 35))   -- applies an aura
  AND s.EffectImplicitTargetA1 IN (0, 1, 5, 20, 21, 30, 31, 35, 37, 45, 56, 57, 61)   -- caster, pet,
  AND s.EffectImplicitTargetA2 IN (0, 1, 5, 20, 21, 30, 31, 35, 37, 45, 56, 57, 61)   -- friend or
  AND s.EffectImplicitTargetA3 IN (0, 1, 5, 20, 21, 30, 31, 35, 37, 45, 56, 57, 61);  -- party only

-- -----------------------------------------------------------------------------
-- 3. Put everything this script has ever changed back to stock, so a name
--    removed from the list above returns to normal.
-- -----------------------------------------------------------------------------
UPDATE spell_template s
JOIN custom_buff_duration_stock k ON k.Id = s.Id
SET s.DurationIndex = k.DurationIndex;

-- -----------------------------------------------------------------------------
-- 4. Raise the buffs on the list to the target duration.
-- -----------------------------------------------------------------------------
UPDATE spell_template s
JOIN custom_buff_duration_stock k ON k.Id = s.Id
JOIN hlb_buff b ON b.name = s.SpellName
SET s.DurationIndex = @target_index
WHERE @target_index <> 0;

DROP TEMPORARY TABLE IF EXISTS hlb_buff;
