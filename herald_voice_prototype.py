"""Reference prototype for docs/proposal_herald_voice.md. Not part of the DM.

What the world database already holds about how one quest-giver talks: their
title and role, and a few of their own stock lines. Run against a test copy.

    python3 herald_voice_prototype.py 197 823 295
"""
import json, re, subprocess, sys
WORLD = "mlive"
ROLES = {4: "vendor", 8: "flight master", 16: "trainer", 128: "innkeeper", 256: "banker", 4096: "repairer", 64: "stable master"}

def rows(sql):
    out = subprocess.run(["mariadb", "-N", "-B", "-r", "-e", sql], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    if out.returncode: sys.exit(out.stderr)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]

def clean(text, limit=260):
    text = " ".join(str(text or "").replace("$B", " ").replace("$b", " ").split())
    if len(text) <= limit: return text
    cut = text[:limit]
    return cut[:max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? ")) + 1] or cut + "..."

def voice(entry, samples=3):
    who = rows(f"""SELECT JSON_OBJECT('name', t.Name, 'title', IFNULL(t.SubName, ''), 'flags', t.NpcFlags, 'level', t.MaxLevel,
                                      'gender', mi.gender, 'gossip', t.GossipMenuId)
                   FROM {WORLD}.creature_template t LEFT JOIN {WORLD}.creature_model_info mi ON mi.modelid = t.DisplayId1
                   WHERE t.Entry = {int(entry)};""")[0]
    lines = rows(f"""
        SELECT JSON_OBJECT('src', 'greeting', 'text', g.Text) FROM {WORLD}.questgiver_greeting g WHERE g.Entry = {int(entry)} AND g.Type = 0
        UNION ALL
        SELECT JSON_OBJECT('src', 'offering a quest', 'text', q.Details) FROM {WORLD}.creature_questrelation r
          JOIN {WORLD}.quest_template q ON q.entry = r.quest WHERE r.id = {int(entry)} AND r.quest < 30000 AND CHAR_LENGTH(q.Details) > 40
        UNION ALL
        SELECT JSON_OBJECT('src', 'at a turn-in', 'text', q.OfferRewardText) FROM {WORLD}.creature_involvedrelation r
          JOIN {WORLD}.quest_template q ON q.entry = r.quest WHERE r.id = {int(entry)} AND r.quest < 30000 AND CHAR_LENGTH(q.OfferRewardText) > 40
        UNION ALL
        SELECT JSON_OBJECT('src', 'small talk', 'text', IF(CHAR_LENGTH(IFNULL(b.Text, '')) > 0, b.Text, b.Text1)) FROM {WORLD}.gossip_menu m
          JOIN {WORLD}.npc_text_broadcast_text n ON n.Id = m.text_id JOIN {WORLD}.broadcast_text b ON b.Id = n.BroadcastTextId0
          WHERE m.entry = {int(who['gossip'])} AND {int(who['gossip'])} <> 0;""")
    picked, seen = [], set()
    order = {"greeting": 0, "offering a quest": 1, "at a turn-in": 2, "small talk": 3}
    for line in sorted(lines, key=lambda l: (order[l["src"]], -len(str(l["text"])))):
        text = clean(line["text"])
        key = re.sub(r"[^a-z]", "", text.lower())[:50]            # near-duplicates share an opening
        if len(text) < 30 or key in seen: continue
        if sum(1 for p in picked if p["src"] == line["src"]) >= (1 if line["src"] != "offering a quest" else 2): continue
        seen.add(key); picked.append({"src": line["src"], "text": text})
        if len(picked) == samples: break
    roles = [name for bit, name in ROLES.items() if who["flags"] & bit]
    facts = ", ".join(x for x in (who["title"], " and ".join(roles), {0: "male", 1: "female"}.get(who["gender"], ""), f"level {who['level']}") if x)
    return {"name": who["name"], "facts": facts, "samples": picked}

if __name__ == "__main__":
    for entry in sys.argv[1:]:
        v = voice(entry)
        print(f"{v['name']} ({v['facts']})")
        for s in v["samples"]: print(f"   [{s['src']}] \"{s['text']}\"")
        if not v["samples"]: print("   (no lines of their own in the database)")
