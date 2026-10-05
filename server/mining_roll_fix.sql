-- Mining can no longer fail on a node the character has the skill for.
-- The four ranks of the Mining spell: Apprentice, Journeyman, Expert, Artisan.
UPDATE spell_template
SET EffectBasePoints1 = 24
WHERE Id IN (2575, 2576, 3564, 10248) AND EffectBasePoints1 = -1;
