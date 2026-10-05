-- =============================================================================
-- dm_props.sql
-- Story props for the Overseer's trophy bounties on CMaNGOS Classic (1.12.1).
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/dm_props.sql, then restart.
--          New item types load only at server start.
-- Design:  docs/proposal_prop_trophies.md in the DM repository.
--
-- What it creates
--   98 generic quest items ("props") in ids 200000 to 200199. The DM adds
--   one to a creature's loot as a quest-only drop when a bounty needs it
--   ("the kobolds stole my books: collect 12"), and removes it afterwards.
--   On their own the props do nothing: nothing drops, sells or asks for them.
--
-- Every prop is
--   * a quest item (class 12, white quality, binds as a quest item, no price)
--   * party loot (flag 2048): each party member can take one from the same corpse
--   * stackable to 100
--   * named generically, so one prop serves many stories
--   * shown with an icon borrowed from a stock item, named beside each row
--
-- Id blocks, with room to add more in each:
--   200000 to 200012   Papers and records (13)
--   200020 to 200033   Valuables (14)
--   200040 to 200059   Supplies and tools (20)
--   200060 to 200087   Taken from the body (28)
--   200100 to 200118   Relics and the uncanny (19)
--   200130 to 200133   Growing things (4)
--
-- To add a prop: append a row in its block with the next free id and the
-- displayid of any stock item whose icon fits, then restart the server.
-- To rename one: edit the name and restart. Do not reuse an id for a
-- different thing; a character may still be carrying the old item.
--
-- Safe to run on every startup: rows are rewritten in place (REPLACE), and
-- only ids listed here are touched.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Definitions: id, name, icon (displayid).
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS dmp_def;
CREATE TEMPORARY TABLE dmp_def (
  entry     MEDIUMINT UNSIGNED NOT NULL PRIMARY KEY,
  name      VARCHAR(255)       NOT NULL,
  displayid MEDIUMINT UNSIGNED NOT NULL
);

-- Papers and records
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200000, 'Stolen Book',       1143),    -- icon: An Old History Book
  (200001, 'Stolen Scroll',     1301),    -- icon: Simple Scroll
  (200002, 'Coded Orders',      13125),   -- icon: Defias Script
  (200003, 'Sealed Letter',     3093),    -- icon: Musty Letter
  (200004, 'Bloodstained Note', 1102),    -- icon: Muddy Note
  (200005, 'Torn Journal Page', 9135),    -- icon: Journal Page
  (200006, 'Worn Journal',      7152),    -- icon: Galen's Journal
  (200007, 'Crude Map',         33399),   -- icon: Crude Map
  (200008, 'Battle Plans',      1323),    -- icon: Foreboding Plans
  (200009, 'Sealed Folder',     7234),    -- icon: Sealed Folder
  (200010, 'Etched Tablet',     18500),   -- icon: Etched Tablet
  (200011, 'Tablet Shard',      7264),    -- icon: Tablet Shard
  (200012, 'Stolen Blueprints', 7629);    -- icon: Rig Blueprints

-- Valuables
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200020, 'Stolen Silver',      7260),    -- icon: Stolen Silver
  (200021, 'Pouch of Gold Dust', 7137),    -- icon: Gold Dust
  (200022, 'Strange Coin',       32301),   -- icon: Zulian Coin
  (200023, 'Stolen Ring',        963),     -- icon: Sarah's Ring
  (200024, 'Stolen Necklace',    9657),    -- icon: Sparkly Necklace
  (200025, 'Stolen Bracelet',    14432),   -- icon: Shiny Bracelet
  (200026, 'Stolen Gem',         7401),    -- icon: Shadowgem
  (200027, 'Looted Lockbox',     9632),    -- icon: Iron Lockbox
  (200028, 'Small Chest',        12331),   -- icon: Small Chest
  (200029, 'Heavy Pouch',        8631),    -- icon: Torwa's Pouch
  (200030, 'Gold Tooth',         6659),    -- icon: A Gold Tooth
  (200031, 'Stolen Medallion',   7425),    -- icon: Elura's Medallion
  (200032, 'Tarnished Badge',    9429),    -- icon: Reethe's Badge
  (200033, 'Marked Insignia',    17655);   -- icon: Mithril Insignia

-- Supplies and tools
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200040, 'Stolen Supplies',      7925),    -- icon: Supply Crate
  (200041, 'Sealed Crate',         8928),    -- icon: Sealed Crate
  (200042, 'Sack of Stolen Grain', 11998),   -- icon: Sack of Rye
  (200043, 'Stolen Cask',          7923),    -- icon: Barrel of Thunder Ale
  (200044, 'Bolt of Stolen Cloth', 7383),    -- icon: Linen Cloth
  (200045, 'Stolen Ingot',         7376),    -- icon: Iron Bar
  (200046, 'Stolen Tools',         7064),    -- icon: Broken Tools
  (200047, 'Lockpicking Kit',      7411),    -- icon: Thieves' Tools
  (200048, 'Stolen Hammer',        14306),   -- icon: Bingles' Hammer
  (200049, 'Miner''s Pick',        23383),   -- icon: Jaron's Pick
  (200050, 'Stolen Candle',        7066),    -- icon: Large Candle
  (200051, 'Guttered Candle',      6677),    -- icon: Melted Candle
  (200052, 'Burnt-out Torch',      12311),   -- icon: Unlit Poor Torch
  (200053, 'Flask of Lamp Oil',    18084),   -- icon: Flask of Oil
  (200054, 'Length of Rope',       10301),   -- icon: Logging Rope
  (200055, 'Brass Compass',        6562),    -- icon: A Simple Compass
  (200056, 'Cracked Spyglass',     7365),    -- icon: Ornate Spyglass
  (200057, 'Crude Key',            8951),    -- icon: Wooden Key
  (200058, 'Shackle Key',          6708),    -- icon: Shackle Key
  (200059, 'Marked Bandana',       1272);    -- icon: Red Burlap Bandana

-- Taken from the body
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200060, 'Severed Head',   1310),    -- icon: Targ's Head
  (200061, 'Severed Ear',    9668),    -- icon: Centaur Ear
  (200062, 'Severed Paw',    6671),    -- icon: Gnoll Paw
  (200063, 'Severed Hoof',   9209),    -- icon: Cloven Hoof
  (200064, 'Bloodied Fang',  2460),    -- icon: Large Fang
  (200065, 'Broken Fang',    6002),    -- icon: Broken Fang
  (200066, 'Hooked Claw',    1496),    -- icon: Sharp Claw
  (200067, 'Blackened Claw', 3146),    -- icon: Wicked Claw
  (200068, 'Curved Tusk',    1225),    -- icon: Large Boar Tusk
  (200069, 'Cracked Horn',   11947),   -- icon: Kodo Horn
  (200070, 'Broken Antler',  7999),    -- icon: Broken Antler
  (200071, 'Staring Eye',    7394),    -- icon: Murloc Eye
  (200072, 'Still Heart',    6693),    -- icon: Raptor Heart
  (200073, 'Tainted Heart',  3422),    -- icon: Tainted Heart
  (200074, 'Forked Tongue',  11889),   -- icon: Forked Tongue
  (200075, 'Leathery Wing',  11489),   -- icon: Duskbat Wing
  (200076, 'Scaly Tail',     20915),   -- icon: Thick Scaly Tail
  (200077, 'Torn Pelt',      7086),    -- icon: Ruined Pelt
  (200078, 'Thick Hide',     8952),    -- icon: Thick Hide
  (200079, 'Glinting Scale', 3668),    -- icon: Naga Scale
  (200080, 'Finger Bone',    7251),    -- icon: Skeleton Finger
  (200081, 'Old Skull',      7741),    -- icon: Old Skull
  (200082, 'Bone Shards',    13806),   -- icon: Bone Fragments
  (200083, 'Venom Sac',      4045),    -- icon: Webwood Venom Sac
  (200084, 'Vial of Ichor',  6690),    -- icon: Slimy Ichor
  (200085, 'Tainted Meat',   6348),    -- icon: Chunk of Boar Meat
  (200086, 'Omen Feather',   28877),   -- icon: Light Feather
  (200087, 'Strange Egg',    18046);   -- icon: Small Egg

-- Relics and the uncanny
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200100, 'Carved Idol',        34153),   -- icon: Onyx Idol
  (200101, 'Crude Charm',        9730),    -- icon: Crude Charm
  (200102, 'Rag Doll',           6358),    -- icon: Rag Doll
  (200103, 'Pinned Doll',        2622),    -- icon: Punctured Voodoo Doll
  (200104, 'Cracked Relic',      13988),   -- icon: Mathystra Relic
  (200105, 'Ritual Cup',         18061),   -- icon: Elven Cup Relic
  (200106, 'Tattered Banner',    6748),    -- icon: Scourge Banner
  (200107, 'Dark Rune',          32905),   -- icon: Dark Rune
  (200108, 'Clouded Soul Gem',   7257),    -- icon: Soul Gem
  (200109, 'Humming Crystal',    15027),   -- icon: Oracle Crystal
  (200110, 'Blood-dark Shard',   7045),    -- icon: Blood Shard
  (200111, 'Glowing Shard',      19223),   -- icon: Glowing Shard
  (200112, 'Carved Stone',       4714),    -- icon: Rough Stone
  (200113, 'Pinch of Tomb Dust', 6371),    -- icon: Tomb Dust
  (200114, 'Vial of Poison',     1288),    -- icon: Venom Bottle
  (200115, 'Bottle of Sickness', 3788),    -- icon: Bottle of Disease
  (200116, 'Sealed Vial',        18077),   -- icon: Empty Vial
  (200117, 'Broken Chain',       4829),    -- icon: Chains of Hematus
  (200118, 'Rusted Shackle',     7132);    -- icon: Glutton Shackle

-- Growing things
INSERT INTO dmp_def (entry, name, displayid) VALUES
  (200130, 'Pale Mushroom',    17871),   -- icon: Ghost Mushroom
  (200131, 'Gnarled Root',     1464),    -- icon: Earthroot
  (200132, 'Strange Flower',   19495),   -- icon: Fast-growing Flower
  (200133, 'Crusted Bandages', 18170);   -- icon: Crusted Bandages

-- -----------------------------------------------------------------------------
-- 2. Build the items. Each is a clone of the stock quest item Red Burlap
--    Bandana (752) with its fields replaced, so this keeps working if upstream
--    adds columns to item_template. If 752 is ever missing, nothing is created.
-- -----------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS dmp_item;
CREATE TEMPORARY TABLE dmp_item AS
SELECT i.*, d.entry AS dmp_new_entry
FROM item_template i
CROSS JOIN dmp_def d
WHERE i.entry = 752;

UPDATE dmp_item s
JOIN dmp_def d ON d.entry = s.dmp_new_entry
SET s.entry       = d.entry,
    s.name        = d.name,
    s.displayid   = d.displayid,
    s.class       = 12,      -- quest item
    s.subclass    = 0,
    s.Quality     = 1,       -- white
    s.Flags       = 2048,    -- party loot
    s.bonding     = 4,       -- binds as a quest item
    s.stackable   = 100,
    s.maxcount    = 0,       -- no carry limit
    s.BuyPrice    = 0,
    s.SellPrice   = 0,
    s.startquest  = 0,
    s.description = '';

ALTER TABLE dmp_item DROP COLUMN dmp_new_entry;

REPLACE INTO item_template SELECT * FROM dmp_item;

DROP TEMPORARY TABLE IF EXISTS dmp_item;
DROP TEMPORARY TABLE IF EXISTS dmp_def;
