-- =============================================================================
-- caster_books.sql
-- Opens every stock caster-class spell book to all six caster classes on
-- CMaNGOS Classic (1.12.1): dropped books, vendor books and quest books alike.
--
-- Target:  world database `mangos` (cmangos-deploy custom-sql hook)
-- Install: storage/classic/database/custom-sql/caster_books.sql
-- Built against: mangos-classic 8ec338a, classic-db z2815
--
-- Supersedes demon_grimoires.sql: the demon trainers' grimoires are warlock
-- books, so this script covers them too. Delete the older file.
--
-- Rule: a book that a Paladin, Priest, Shaman, Mage, Warlock or Druid can
-- use becomes usable by all six.
--   1490 = Paladin(2) + Priest(16) + Shaman(64) + Mage(128) + Warlock(256)
--          + Druid(1024)
-- Warrior, Hunter and Rogue books are left alone (13 = 1 + 4 + 8), as are
-- books with no class restriction (-1).
--
-- The class mask lives on the item, so it applies however the book is
-- obtained. For bind-on-pickup books it also controls whether a vendor
-- lists them for the buyer's class.
--
-- Safe to run on every startup: OR-ing the same bits again changes nothing.
-- =============================================================================

UPDATE item_template
SET AllowableClass = AllowableClass | 1490
WHERE class = 9                          -- recipes and books
  AND subclass = 0                       -- books
  AND spellid_1 > 0                      -- teaches something when used
  AND AllowableClass > 0                 -- has a class restriction
  AND (AllowableClass & 1490) <> 0       -- belongs to a caster class
  AND (AllowableClass & 13) = 0;         -- not a Warrior, Hunter or Rogue book
