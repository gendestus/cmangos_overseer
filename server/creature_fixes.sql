-- =============================================================================
-- creature_fixes.sql
-- Tames four low-level creatures whose melee damage is far out of line with
-- their neighbours on CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/creature_fixes.sql, then restart.
--          Creature data loads only at server start.
-- Built against: classic-db ec4f596 (2026-09-22) with all updates applied.
--
-- Background
--   Every creature type has a damage multiplier; the server computes melee
--   damage from it. The upstream content update 4835_charm_cls.sql raised it
--   sharply for these four, so a level 5 Defias Cutpurse hits for 32 to 35
--   where the boars beside it hit for 7 to 9. This script puts each one back
--   to the value the previous upstream update (4833_cls_rework.sql) gave it.
--
--   It is not known whether the upstream values are a mistake. This is a
--   deliberate local override, not a correction of a confirmed bug.
--
-- Safe to run on every startup, and self-retiring: a row is changed only
-- while its multiplier is still the upstream value listed below. If upstream
-- changes a creature again, this script leaves that creature alone.
--
-- To add a creature: add a row in section 1 with its current multiplier as
-- both bounds (a little either side, since the column is a float) and the
-- multiplier you want.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. The overrides.
--    upstream_low / upstream_high bracket the multiplier this script expects
--    to find. restored is the value from the earlier upstream update.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS cfx_fix;
CREATE TEMPORARY TABLE cfx_fix (
  entry         MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  upstream_low  FLOAT NOT NULL,
  upstream_high FLOAT NOT NULL,
  restored      FLOAT NOT NULL
);

INSERT INTO cfx_fix (entry, upstream_low, upstream_high, restored) VALUES
  (94,   4.95, 5.05, 1.4632803),   -- Defias Cutpurse,     level 5-6,   was hitting for 32-35
  (1083, 5.95, 6.05, 2.0000024),   -- Murloc Shorestriker, level 16-17, was hitting for 83-91
  (171,  4.05, 4.15, 2.9999917),   -- Murloc Warrior,      level 15-16, was hitting for 47-63
  (426,  3.35, 3.45, 2.7000046);   -- Redridge Brute,      level 17-18, was hitting for 42-56

-- -----------------------------------------------------------------------------
-- 2. Apply. The stored damage figures are scaled by the same ratio so the row
--    stays consistent; the server itself works from the multiplier. The
--    multiplier is assigned last because the lines above it read its old value.
-- -----------------------------------------------------------------------------
UPDATE creature_template t
JOIN cfx_fix f ON f.entry = t.Entry
SET t.MinMeleeDmg      = t.MinMeleeDmg * (f.restored / t.DamageMultiplier),
    t.MaxMeleeDmg      = t.MaxMeleeDmg * (f.restored / t.DamageMultiplier),
    t.DamageVariance   = 0.4,          -- the earlier update's value for all four
    t.DamageMultiplier = f.restored
WHERE t.DamageMultiplier BETWEEN f.upstream_low AND f.upstream_high;

DROP TEMPORARY TABLE IF EXISTS cfx_fix;
