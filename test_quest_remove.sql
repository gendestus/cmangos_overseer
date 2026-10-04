-- Remove DM quest 30000: The Overseer's First Test
-- Apply, run `.reload all_quest`, and have anyone holding the quest relog.

USE mangos;

DELETE FROM creature_questrelation WHERE quest = 30000;
DELETE FROM creature_involvedrelation WHERE quest = 30000;
DELETE FROM quest_template WHERE entry = 30000;

-- Forget every character's progress and completion of it.
DELETE FROM characters.character_queststatus WHERE quest = 30000;

