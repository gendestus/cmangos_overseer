# AI Dungeon Master for CMaNGOS Classic: design spec

Status: the original design, written 2026-10-03 before anything was built. It is kept as the record of why the DM is shaped this way. For what exists now, read `../README.md`; for what is next, read `DEV_PLAN.md`.

Where this document and the code disagree, the code is right. The main changes since it was written:

- Stages 0 and 1 are built: remote console, hot-loaded quests, the watch loop, story memory and approval.
- Quest givers are not placed heralds. Each bounty is offered through a nearby friendly NPC picked from the live world.
- Friend or foe is read from the server's faction files, not guessed by rule.
- Each character has a private mini-arc, and proposals can be auto-approved per type.

Target: the cmangos-deploy server on ADA (CMaNGOS Classic, core 8ec338a or later), 1 to 3 human players.

## 1. Goal

An AI overseer that watches where the players are and what they have done, then creates challenges and rewards that fit: bounties, named encounters, letters, and capstone spell books.

Not goals for now: real-time reactions (seconds), controlling monsters in combat, or anything that needs the AI to "see" the screen.

## 2. What the server allows

Every line in this section was checked against the server source or the world database. "Console" means the server's remote command interface, which needs no logged-in character.

### 2.1 The remote interface

- The server has two remote command interfaces, SOAP (port 7878) and RA (port 3443). Both are off by default; cmangos-deploy documents how to switch them on.
- SOAP authenticates with a game account name and password and requires a minimum account level.
- Of 515 GM commands, 230 run from the console. The rest need a GM character standing in the world.

### 2.2 What the DM can do, by channel

| Capability | Live, from console | Live, by editing the database then reloading | Needs a restart | Needs an in-game GM |
|---|---|---|---|---|
| Mail a letter, items or gold | `send mail`, `send items`, `send money` | | | |
| Speak to players | `announce`, `notify`, `send message` | | | |
| Create or change a quest | | `quest_template` and its giver tables, then `reload all_quest` | | |
| Start or stop a pre-built encounter | `event start`, `event stop` | | | |
| Change a vendor's stock | | `npc_vendor`, then `reload npc_vendor` | | |
| Change what a creature drops | | `creature_loot_template`, then reload | | |
| Teleport a player to a named place | `tele name` (place list reloadable) | | | |
| Force a save so positions are current | `saveall` | | | |
| New creature types, new spawns, new encounters | | | Yes | |
| New item types (custom rewards) | | | Yes | |
| Spawn an NPC at an arbitrary spot right now | | | | Yes |
| Teach a spell directly | | | | Yes (but see 4.2) |
| Weather, NPC speech and emotes | | | | Yes |

The important finding: quests are hot-reloadable. The DM can write a new quest, with its own text, objectives and rewards, and have it on offer minutes later with no restart and no client patch, because the 1.12 client asks the server for quest text.

### 2.3 What the DM can see

| Source | What it gives | Freshness |
|---|---|---|
| `characters` table | Name, race, class, level, XP, money, map, zone, position, online flag, health | Saved every 15 minutes by default; `saveall` forces it |
| `character_queststatus` | Progress and completion of each quest, including DM-written ones | As above |
| `creature_respawn` | Which permanent spawns are currently dead | Written at death |
| `mail` | Letters players send to a DM-owned character | Immediate |
| World database | Every creature, item, quest and spawn, for choosing targets and rewards | Static |

There is no chat log in the server config, so in-game mail to a DM character is the players' way to talk to it.

## 3. Architecture

One new service, `dm`, added to the same compose project as the game server.

```
players <-> mangosd <-> MariaDB (world, characters)
                ^             ^
          SOAP  |             | SQL: read-only for sensing,
                |             | narrow write access for quests/vendors/loot
              +-----------------+
              |   dm service    |---- LLM endpoint (local or API)
              +-----------------+
                |           |
           state store   audit log
```

- **Language:** Python. It needs an HTTP client for SOAP, a MariaDB client, and an LLM client.
- **Network:** SOAP stays on the internal compose network; the port is not published to the LAN.
- **Accounts:** a dedicated game account for SOAP, a read-only database user for sensing, and a second database user that can write only to the handful of tables in section 4.
- **State store:** the DM's own SQLite file: what it has issued, to whom, when, and its notes about each character.

### 3.1 The loop

Every few minutes:

1. `saveall`, then read a snapshot of the players.
2. Diff against the last snapshot: level-ups, zone changes, quests finished, encounters cleared, new mail.
3. Give the LLM the diff, the DM's notes, and the catalog of allowed actions.
4. The LLM returns tool calls, not free text and never raw SQL.
5. A validator checks each call against the guardrails in section 6.
6. Approved calls execute; everything is written to the audit log.

## 4. Content the DM produces

### 4.1 Bounties (live)

A DM-written quest offered by a herald NPC placed in each hub.

- **Objectives:** kill N of a stock creature, collect a stock item, or kill a named DM boss from 4.3.
- **Text:** title, briefing and completion text written by the LLM.
- **Rewards:** gold, a stock item, a spell book, or a spell taught on completion.
- **Fit:** chosen from creatures within a few levels of the player and near their current zone, using the world database.
- **With 48-hour respawns:** the DM can check `creature_respawn` first and avoid sending a player after something that is already dead.
- **Announcing:** every new bounty is announced by letter or on-screen message, because the quest marker does not appear for players already near the herald.

### 4.2 Spell rewards

Two ways to hand out a capstone spell with no GM character:

- Mail the capstone book (`send items`). The 68 capstone books already exist.
- Make the spell a quest reward. Stock data already has 259 quests that teach a spell on completion, so the mechanism is native.

### 4.3 Encounters (prepared in batches)

A named boss or ambush the DM can switch on.

- Each encounter is a "game event" with its own spawns, authored as SQL.
- New encounters only load at a restart, so the DM writes them into a generated custom-sql file and they go live at the next scheduled restart.
- Once loaded, `event start` and `event stop` turn one on or off live, and `creature_respawn` shows when it has been cleared.

### 4.4 Other live levers

- **Letters:** story, warnings and rewards by mail.
- **A travelling merchant:** one vendor whose stock the DM rotates.
- **Seeded drops:** add a capstone book to the loot of a boss the party is heading for.
- **Summons:** teleport a consenting player to an encounter site.

## 5. Tool catalog (what the LLM may call)

| Tool | Mechanism | Key parameters |
|---|---|---|
| `get_party_state` | SQL read | none |
| `find_targets` | SQL read | zone, level range, creature type, alive only |
| `find_rewards` | SQL read | class, level, quality ceiling |
| `read_dm_mail` | SQL read | since |
| `create_bounty` | SQL write + `reload all_quest` | target, count, text, reward |
| `retire_bounty` | SQL write + reload | quest id |
| `send_letter` | `send mail` | character, subject, body |
| `send_reward` | `send items` / `send money` | character, items or gold, reason |
| `broadcast` | `announce` / `notify` | text |
| `start_encounter` / `stop_encounter` | `event start` / `event stop` | encounter id |
| `set_merchant_stock` | SQL write + `reload npc_vendor` | item list |
| `seed_drop` | SQL write + loot reload | creature, item, chance |
| `propose_encounter` | writes to the next nightly SQL batch | boss template, location, level |
| `remember` | DM state store | character, note |

## 6. Guardrails

- **Whitelist only.** The LLM can call the tools above and nothing else. SQL is built from fixed templates with validated parameters.
- **Level bands.** Targets and rewards must fall within set ranges of the player's level.
- **Reward budget.** A cap per character per day on gold, item quality and capstones.
- **Rate limits.** A cap on actions per hour and on active bounties per character.
- **Dry-run and approval modes.** Start with the DM proposing and a human approving; move to automatic once it behaves.
- **Kill switch.** One flag stops all actions; one script removes everything the DM has created.
- **Reserved ID ranges.** All DM content lives in its own ranges so it can be found and removed.
- **Audit log.** Every observation, decision and command, with the LLM's stated reason.

## 7. ID ranges and known server limits

Two bugs so far came from limits that only show up when the server loads data, so each range below needs the server code checked before use.

| Content | Proposed range | Limit to respect |
|---|---|---|
| Quests | 30000 to 39999 | Server uses 32-bit quest ids; stock max is 9665. Client behaviour above that is unverified. |
| Game events | 200 to 999 | Server reads event ids as 16-bit and sizes a table by the highest id; stock max is 1024. |
| Vendor filter conditions | below 65536 | Server reads these as 16-bit (the bug we hit). |
| Creature templates | 91000 and up | 90000 to 90017 already used. |
| Spawns | 12000100 and up | Server limit 16777215; 12000001 to 12000011 already used. |
| Items | 200000 and up | Books use 100000 plus the teach spell id. |

## 8. Stages

| Stage | What exists at the end | Rough effort with a coding agent |
|---|---|---|
| 0. Plumbing | SOAP on, database users, a command-line tool that runs any whitelisted action by hand | 1 to 2 evenings. Mostly done: console, allow-list and quest apply/remove work; dedicated database users are still to do |
| 1. Live DM | The loop, bounties, letters, rewards, announcements, approval mode | A weekend |
| 1.5. Nightly batch | DM-proposed bosses and encounters, generated SQL, scheduled restart | A weekend |
| 2. A body in the world | A headless GM client that can stand somewhere, spawn, speak and teach | Open-ended; depends on benilla's protocol code |

Stage 2 is the only part that needs a game client. Everything before it is a database client and an HTTP client.

## 9. Spikes to run first

Each is under an hour and removes one unknown.

1. DONE. Enable SOAP and run `server info`. Passed from the host on 2026-10-03: `console.py` reached the live server, `apply_quest.py` wrote a quest, reloaded and announced it, and `--remove` took it down.
2. DONE. Insert one custom quest by SQL, `reload all_quest`, and accept it in benilla. Passed: hot quests, the quest id range, client display, and a capstone book as the reward.
3. Build one test event with a single spawn and `event start` it. Note how long it stays up and what `event stop` does.
4. `send items` a capstone book to a character.
5. Mail a letter to a DM-owned character and read it back from the `mail` table.

## 10. Open decisions

1. **Where the model runs:** a local model on ADA or a hosted API. This sets cost, latency and how capable the tool-calling is.
2. **Autonomy:** how long the DM stays in approval mode.
3. **Voice:** who the Overseer is in the fiction, and how often it speaks.
4. **Generosity:** the reward budget, especially how often a capstone appears.
5. **Scope of meddling:** whether the DM may change vendor stock and loot, or only quests and mail.
6. **Restart window:** when the nightly batch may restart the server.

## 11. Risks and unknowns

- **Forced events:** the code shows `event start` works on any valid event, but how long a forced event stays active is unverified. Spike 3 answers it.
- **Reloading quests with players online:** TESTED 2026-10-03. A hot-loaded quest (id 30000) was offered, completed and rewarded in benilla, and a reload did not disturb quest progress already under way.
- **Quest markers are not pushed.** The server sends the "!" over a quest giver only when a client asks, which happens when the NPC comes into view. A character already standing nearby does not see a new quest's marker until the NPC re-enters view (walk away and back, or relog). The DM must announce each new bounty itself.
- **Position freshness:** `saveall` on every loop is assumed cheap for three players; unmeasured.
- **Model quality:** a small local model may write good text but choose poor targets; the validator limits the damage, not the dullness.
- **Unsupervised writes to a live database:** the reason for approval mode, narrow database permissions and the audit log.
