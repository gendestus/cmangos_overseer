-- Defias Cutpurse: upstream update 4835 set the damage multiplier to 5.
-- Put it back to the value the previous upstream update used.
UPDATE creature_template
SET DamageMultiplier = 1.4632803, DamageVariance = 0.4,
    MinMeleeDmg = 9.26, MaxMeleeDmg = 10.26
WHERE Entry = 94 AND DamageMultiplier = 5;
