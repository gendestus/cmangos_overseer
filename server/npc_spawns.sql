-- =============================================================================
-- npc_spawns.sql
-- Makes the hand-placed custom NPCs permanent on CMaNGOS Classic (1.12.1), so
-- they survive a world database rebuild.
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/npc_spawns.sql
-- Source:  positions read from the live `creature` table on 2026-10-03.
--
-- How it works
--   * Each spawn gets a fixed id in a reserved range (12000001 and up). Stock
--     data tops out near 5.3 million and the server's limit is 16777215.
--   * The copy you placed by hand with .npc add is removed, so there are not
--     two of each NPC on the same spot. The delete matches the exact spawn id
--     AND creature id you placed, so nothing else can be removed by it.
--   * Respawn timer is 30 seconds, which keeps these NPCs out of the 48-hour
--     rule in long_respawn.sql (that script skips timers of 30s or less).
--
-- To move an NPC: change its coordinates here (or re-place it, re-run the
-- query and regenerate this file), then restart.
-- To remove one for good: delete its line here. Deleting it in game is not
-- enough, because this file puts it back at the next start.
--
-- Safe to run on every startup.
-- =============================================================================

-- 1. Remove the hand-placed copies these rows replace.
DELETE FROM creature
WHERE (guid, id) IN (
  (5331303, 4986),
  (5331302, 4991),
  (5331305, 90000),
  (5331307, 90010),
  (5331308, 90011),
  (5331311, 90012),
  (5331312, 90013),
  (5331309, 90014),
  (5331313, 90015),
  (5331314, 90016),
  (5331310, 90017)
);

-- 2. The permanent spawns. All are on map 0 (Eastern Kingdoms).
REPLACE INTO creature
  (guid, id, map, spawnMask, position_x, position_y, position_z, orientation, spawntimesecsmin, spawntimesecsmax, spawndist, MovementType)
VALUES
  -- Northshire
  (12000001, 4986,  0, 1, -8879.41, -221.749, 81.7269, 4.60374,  30, 30, 0, 0),  -- World Hunter Trainer
  (12000002, 4991,  0, 1, -8903.68, -110.681, 81.8681, 3.895,    30, 30, 0, 0),  -- World Shaman Trainer
  (12000003, 90000, 0, 1, -8922.91, -99.6111, 83.6225, 5.11449,  30, 30, 0, 0),  -- Arcane Bookseller (test vendor)
  -- Stormwind
  (12000004, 90010, 0, 1, -9018.18, 874.779,  29.6207, 3.76571,  30, 30, 0, 0),  -- Tessa Quillwright, mage tomes
  (12000005, 90011, 0, 1, -8988.19, 1037.01,  101.424, 5.93378,  30, 30, 0, 0),  -- Malrick Vane, warlock grimoires
  (12000006, 90012, 0, 1, -8503.37, 801.721,  106.539, 5.2245,   30, 30, 0, 0),  -- Sister Adelyn, priest codices
  (12000007, 90013, 0, 1, -8579.57, 868.963,  106.537, 0.265821, 30, 30, 0, 0),  -- Sir Corwin Hale, paladin librams
  (12000008, 90014, 0, 1, -8774.06, 1101.88,  92.5523, 4.85816,  30, 30, 0, 0),  -- Lirael Mossglade, druid spells
  (12000009, 90015, 0, 1, -8711.53, 1009.38,  96.6966, 3.82423,  30, 30, 0, 0),  -- Borgan Stormhand, shaman spells
  (12000010, 90016, 0, 1, -8710.55, 1005.72,  96.8966, 2.67774,  30, 30, 0, 0),  -- Hilda Stormhand, shaman totems
  (12000011, 90017, 0, 1, -8781.64, 1084,     92.5392, 0.705032, 30, 30, 0, 0);  -- Garrick Thornpaw, druid forms
