-- =============================================================================
-- more_nodes.sql
-- More ore veins and herbs standing in the world at once on CMaNGOS Classic
-- (1.12.1), by raising the pool limits that hold them back.
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/more_nodes.sql, then restart.
--          Pool data loads only at server start.
-- Built against: classic-db ec4f596 (2026-09-22) with all updates applied.
--
-- Background
--   Nearly every node location belongs to a pool: a set of possible places of
--   which only max_limit are active at a time. In stock data about 14% of ore
--   locations and 19% of herb locations are up at any moment.
--
--   Herbs:  one level. Each herb pool holds about nine locations and keeps
--           one or two active.
--   Ore:    two levels for most of it. An inner pool is one spot that can be
--           one of several vein types, and always has exactly one active. A
--           zone pool above decides how many of those spots are switched on.
--
-- What this changes (pool_template.max_limit only)
--   A. Herb pools            limit x herb factor, capped at the pool's distinct places
--   B. Standalone ore pools  limit x ore factor, capped at the pool's distinct places
--   C. Ore zone pools        limit x ore factor, capped at the number of spots
--
-- What it never changes
--   * Inner ore pools. Raising those would put two veins on the same spot.
--   * A limit beyond the places available: the cap in A and B counts distinct
--     positions, so two nodes are never active on one spot.
--   * Pools that mix nodes with anything else. They are left alone.
--   * Respawn timers, which stay at stock (five minutes for nearly all nodes).
--   * Nodes placed outside any pool. Those are always up already.
--
-- What to expect
--   Limits are small whole numbers, mostly 1 or 2, so the steps are coarse,
--   and some pools cannot grow: 154 ore pools are a single spot that is
--   always up, and 408 herbs sit outside any pool. Measured on stock data,
--   counting those:
--       factor 2:  ore 911 -> about 1,576 (+73%)   herbs 1,627 -> about 2,799 (+72%)
--       factor 3:  ore      -> about 2,089 (+129%)  herbs       -> about 3,897 (+140%)
--
-- TUNING
--   @ore_factor and @herb_factor below multiply each pool's limit:
--   2 doubles it, 3 triples it, 1 is stock. They are independent.
--   Every run works from the remembered stock limits, so changing a factor
--   and re-running never compounds.
--
-- Safe to run on every startup.
-- =============================================================================

SET @ore_factor  := 2;
SET @herb_factor := 2;

-- -----------------------------------------------------------------------------
-- 1. Which spawns are nodes.
--    Ore:   chests named "... Vein" or "... Deposit".
--    Herbs: chests whose loot always contains a herb (trade good, subclass 9).
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS mn_type;
CREATE TEMPORARY TABLE mn_type (
  entry MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  kind  TINYINT UNSIGNED NOT NULL              -- 1 ore, 2 herb
);

INSERT IGNORE INTO mn_type (entry, kind)
SELECT t.entry, 1
FROM gameobject_template t
WHERE t.type = 3 AND (t.name LIKE '% Vein' OR t.name LIKE '% Deposit');

INSERT IGNORE INTO mn_type (entry, kind)
SELECT DISTINCT t.entry, 2
FROM gameobject_template t
JOIN gameobject_loot_template l ON l.entry = t.data1
JOIN item_template i ON i.entry = l.item
WHERE t.type = 3 AND i.class = 7 AND i.subclass = 9 AND l.ChanceOrQuestChance = 100;

-- -----------------------------------------------------------------------------
-- 2. Every pool that holds node spawns: how many members, how many of each
--    kind, and how many distinct places.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS mn_pool;
CREATE TEMPORARY TABLE mn_pool (
  pool    MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  members INT NOT NULL,
  ore     INT NOT NULL,
  herb    INT NOT NULL,
  places  INT NOT NULL
);

INSERT INTO mn_pool (pool, members, ore, herb, places)
SELECT pg.pool_entry,
       COUNT(*),
       SUM(IFNULL(n.kind, 0) = 1),
       SUM(IFNULL(n.kind, 0) = 2),
       COUNT(DISTINCT g.map, ROUND(g.position_x), ROUND(g.position_y))
FROM pool_gameobject pg
JOIN gameobject g ON g.guid = pg.guid
LEFT JOIN mn_type n ON n.entry = g.id
GROUP BY pg.pool_entry
HAVING SUM(n.kind IS NOT NULL) > 0;

-- A second copy, because a temporary table cannot be read twice in one query.
DROP TEMPORARY TABLE IF EXISTS mn_pool2;
CREATE TEMPORARY TABLE mn_pool2 AS SELECT * FROM mn_pool;
ALTER TABLE mn_pool2 ADD PRIMARY KEY (pool);

-- -----------------------------------------------------------------------------
-- 3. The pools to raise, each with the highest limit that makes sense for it.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS mn_target;
CREATE TEMPORARY TABLE mn_target (
  pool MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  cap  INT NOT NULL,
  herb TINYINT UNSIGNED NOT NULL               -- 1 herb pool, 0 ore pool or ore zone pool
);

-- A and B: pools made only of herbs, or only of ore, that are not inside
-- another pool.
INSERT INTO mn_target (pool, cap, herb)
SELECT p.pool, p.places, IF(p.herb > 0, 1, 0)
FROM mn_pool p
LEFT JOIN pool_pool pp ON pp.pool_id = p.pool
WHERE pp.pool_id IS NULL
  AND (p.herb = p.members OR p.ore = p.members);

-- C: ore zone pools. A zone pool qualifies when every child that holds
-- anything is an ore-only pool. Stock data has leftover child pools with no
-- spawns at all; those are ignored, and do not count toward the cap.
DROP TEMPORARY TABLE IF EXISTS mn_other;
CREATE TEMPORARY TABLE mn_other (
  pool MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY     -- pools holding something that is not ore
);

INSERT IGNORE INTO mn_other (pool)
SELECT pg.pool_entry
FROM pool_gameobject pg
JOIN gameobject g ON g.guid = pg.guid
LEFT JOIN mn_pool p ON p.pool = pg.pool_entry AND p.ore = p.members
WHERE p.pool IS NULL;

INSERT IGNORE INTO mn_other (pool) SELECT pool_entry FROM pool_gameobject_template;
INSERT IGNORE INTO mn_other (pool) SELECT pool_entry FROM pool_creature;
INSERT IGNORE INTO mn_other (pool) SELECT pool_entry FROM pool_creature_template;
INSERT IGNORE INTO mn_other (pool) SELECT mother_pool FROM pool_pool;

INSERT IGNORE INTO mn_target (pool, cap, herb)
SELECT pp.mother_pool, SUM(c.pool IS NOT NULL), 0
FROM pool_pool pp
LEFT JOIN mn_pool2 c ON c.pool = pp.pool_id AND c.ore = c.members
LEFT JOIN mn_other o ON o.pool = pp.pool_id
GROUP BY pp.mother_pool
HAVING SUM(o.pool IS NOT NULL) = 0 AND SUM(c.pool IS NOT NULL) > 0;

-- -----------------------------------------------------------------------------
-- 4. Remember each pool's stock limit the first time it is seen. This table
--    lives in the world database, so a world rebuild resets it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS custom_node_pool_stock (
  entry     MEDIUMINT UNSIGNED NOT NULL,
  max_limit INT UNSIGNED NOT NULL,
  PRIMARY KEY (entry)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COMMENT='Stock node pool limits (more_nodes.sql)';

INSERT IGNORE INTO custom_node_pool_stock (entry, max_limit)
SELECT pt.entry, pt.max_limit
FROM pool_template pt
JOIN mn_target t ON t.pool = pt.entry;

-- -----------------------------------------------------------------------------
-- 5. Put every pool this script has ever changed back to stock, then apply
--    the factors. A limit is never lowered below stock and never raised above
--    its cap.
-- -----------------------------------------------------------------------------
UPDATE pool_template pt
JOIN custom_node_pool_stock k ON k.entry = pt.entry
SET pt.max_limit = k.max_limit;

UPDATE pool_template pt
JOIN custom_node_pool_stock k ON k.entry = pt.entry
JOIN mn_target t ON t.pool = pt.entry
SET pt.max_limit = GREATEST(k.max_limit,
                            LEAST(t.cap, ROUND(k.max_limit * IF(t.herb = 1, @herb_factor, @ore_factor))));

DROP TEMPORARY TABLE IF EXISTS mn_target;
DROP TEMPORARY TABLE IF EXISTS mn_other;
DROP TEMPORARY TABLE IF EXISTS mn_pool2;
DROP TEMPORARY TABLE IF EXISTS mn_pool;
DROP TEMPORARY TABLE IF EXISTS mn_type;
