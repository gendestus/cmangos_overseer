-- =============================================================================
-- long_respawn.sql
-- Open-world creatures stay dead for 48 hours on CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/long_respawn.sql
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- TUNING: change @respawn_secs below and restart. Every run recomputes from
-- the stock timers, so the value can go up or down. 0 restores stock timers.
--
-- Scope:
--   * Maps 0 (Eastern Kingdoms) and 1 (Kalimdor) only. Dungeons, raids and
--     battlegrounds keep stock timers: instance resets govern those, and
--     encounter scripts depend on short timers.
--   * Spawns whose stock timer is 30 seconds or less are left alone. Those
--     are almost all script machinery (triggers, event actors), not mobs.
--   * Timers are only ever raised. A rare or world boss that already takes
--     longer than @respawn_secs keeps its stock timer.
--   * Game objects (herbs, ore, chests) are not touched.
--
-- Takes effect for creatures killed after the next mangosd start.
-- =============================================================================

SET @respawn_secs := 3600;  -- 48 hours

-- Snapshot of the stock timers, taken the first time each spawn is seen.
-- Lives in the world database, so a world rebuild resets it with everything
-- else.
CREATE TABLE IF NOT EXISTS custom_respawn_stock (
  guid             INT UNSIGNED NOT NULL,
  spawntimesecsmin INT UNSIGNED NOT NULL,
  spawntimesecsmax INT UNSIGNED NOT NULL,
  PRIMARY KEY (guid)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COMMENT='Stock creature respawn timers (long_respawn.sql)';

INSERT IGNORE INTO custom_respawn_stock (guid, spawntimesecsmin, spawntimesecsmax)
SELECT guid, spawntimesecsmin, spawntimesecsmax
FROM creature;

-- Recompute every spawn from its stock timer. In-scope spawns are raised to
-- @respawn_secs; everything else is written back to stock, which also undoes
-- an earlier run if the scope rules above are ever changed.
UPDATE creature c
JOIN custom_respawn_stock s ON s.guid = c.guid
SET
  c.spawntimesecsmin = IF(c.map IN (0, 1) AND s.spawntimesecsmin > 30,
                          GREATEST(s.spawntimesecsmin, @respawn_secs),
                          s.spawntimesecsmin),
  c.spawntimesecsmax = IF(c.map IN (0, 1) AND s.spawntimesecsmin > 30,
                          GREATEST(s.spawntimesecsmax, @respawn_secs),
                          s.spawntimesecsmax);
