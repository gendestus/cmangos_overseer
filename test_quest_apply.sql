-- Hot-loaded DM quest 30000: The Overseer's First Test
-- Apply to the live world database, then run in game or on the console:
--     .reload all_quest
-- Safe to apply again: the quest row and its giver/ender links are replaced.

USE mangos;

-- 1. Preflight: every row should say ok. A PROBLEM row means the quest points
--    at something that is missing; the server will log it and the quest may
--    not be completable.
SELECT 'giver 4991 exists and gives quests' AS preflight, IF(EXISTS(SELECT 1 FROM creature_template WHERE Entry = 4991 AND (NpcFlags & 2) <> 0), 'ok', 'PROBLEM') AS result
UNION ALL
SELECT 'giver 4991 is spawned' AS preflight, IF(EXISTS(SELECT 1 FROM creature WHERE id = 4991), 'ok', 'PROBLEM') AS result
UNION ALL
SELECT 'ender 4991 exists and gives quests' AS preflight, IF(EXISTS(SELECT 1 FROM creature_template WHERE Entry = 4991 AND (NpcFlags & 2) <> 0), 'ok', 'PROBLEM') AS result
UNION ALL
SELECT 'kill target 6 exists' AS preflight, IF(EXISTS(SELECT 1 FROM creature_template WHERE Entry = 6), 'ok', 'PROBLEM') AS result
UNION ALL
SELECT 'kill target 6 is spawned' AS preflight, IF(EXISTS(SELECT 1 FROM creature WHERE id = 6), 'ok', 'PROBLEM') AS result
UNION ALL
SELECT 'item 111520 exists' AS preflight, IF(EXISTS(SELECT 1 FROM item_template WHERE entry = 111520), 'ok', 'PROBLEM') AS result;

-- 2. The quest. Columns not listed take their table defaults, so a re-apply
--    always produces the same row.
REPLACE INTO quest_template (
  entry,
  Method,
  ZoneOrSort,
  MinLevel,
  QuestLevel,
  QuestFlags,
  Title,
  Details,
  Objectives,
  RequestItemsText,
  OfferRewardText,
  RewOrReqMoney,
  RewMoneyMaxLevel,
  RewSpell,
  RewSpellCast,
  ReqCreatureOrGOId1,
  ReqCreatureOrGOCount1,
  ReqCreatureOrGOId2,
  ReqCreatureOrGOCount2,
  ReqCreatureOrGOId3,
  ReqCreatureOrGOCount3,
  ReqCreatureOrGOId4,
  ReqCreatureOrGOCount4,
  ReqItemId1,
  ReqItemCount1,
  ReqItemId2,
  ReqItemCount2,
  ReqItemId3,
  ReqItemCount3,
  ReqItemId4,
  ReqItemCount4,
  RewItemId1,
  RewItemCount1,
  RewItemId2,
  RewItemCount2,
  RewItemId3,
  RewItemCount3,
  RewItemId4,
  RewItemCount4,
  RewChoiceItemId1,
  RewChoiceItemCount1,
  RewChoiceItemId2,
  RewChoiceItemCount2,
  RewChoiceItemId3,
  RewChoiceItemCount3,
  RewChoiceItemId4,
  RewChoiceItemCount4,
  RewChoiceItemId5,
  RewChoiceItemCount5,
  RewChoiceItemId6,
  RewChoiceItemCount6
) VALUES (
  30000,
  2,
  9,
  1,
  2,
  8,
  'The Overseer''s First Test',
  'So you are $N. I have been told to expect a $C with more curiosity than sense.$B$BSomething new is watching this valley, and it wants to know what you are made of. The kobolds east of the abbey will do for a first measure. Thin them out and come back to me.',
  'Slay 4 Kobold Vermin in Northshire Valley, then return to the Overseer''s herald.',
  'The Overseer is patient, $N. The kobolds are not going anywhere. Well, four of them are.',
  'Four fewer vermin, and you are still standing. The Overseer has taken note.$B$BTake this. It was never meant for a $C, which is exactly why you are getting it.',
  500,
  102,
  0,
  0,
  6,
  4,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  111520,
  1,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0,
  0
);

-- 3. Who offers it and who takes the turn-in.
DELETE FROM creature_questrelation WHERE quest = 30000;
INSERT INTO creature_questrelation (id, quest) VALUES (4991, 30000);

DELETE FROM creature_involvedrelation WHERE quest = 30000;
INSERT INTO creature_involvedrelation (id, quest) VALUES (4991, 30000);

