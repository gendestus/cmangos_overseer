# The Overseer: an AI dungeon master for CMaNGOS Classic

A small service that watches the players on a private CMaNGOS Classic (1.12.1) server, remembers what they do, and writes bounty quests that continue each character's story. Quests go live in the running game with no restart and no client patch.

It runs beside a [cmangos-deploy](https://github.com/mserajnik/cmangos-deploy) server and is built for one to three players. Design background is in `docs/ai_dm_spec.md`.

## How it works

1. **Observe.** A tick forces a save and reads the game databases: who is online, where, what they finished, who they are grouped with.
2. **Remember.** Anything new goes into the DM's own memory file, `state.db`: a chronicle per character, every bounty and how it ended, and a running story summary.
3. **Plan.** The first time a character is noticed, the model writes a private mini-arc for them: what the Overseer wants them to become over the next few levels, the enemy the story turns toward, and two to four beats. When an arc ends, the next is planned from how it actually went, so the story can turn.
4. **Propose.** When a character has no bounty out, the model is given the arc, the story so far, and real nearby NPCs, creatures and rewards. It picks which NPC the Overseer speaks through, writes the bounty, and the proposal is stored, not applied.
5. **Approve.** You review and approve. Only then is the quest written to the game, reloaded through the server's remote console, and announced.

One bounty at a time: a character with a bounty offered or accepted gets no new proposal. An accepted bounty never expires; one nobody accepts is dropped after a day.

By default nothing reaches the game without `approve`. `DM_AUTO_APPROVE` can switch review off per proposal type.

## Requirements

- A running cmangos-deploy Classic server, with shell access to its host.
- Python 3.8 or later on that host. Only the standard library is used; there is nothing to `pip install`.
- A user who can run `docker compose` in the server folder.
- A Claude API key.

## Repository layout

| Path | What it is |
|---|---|
| `dm.py` | The loop and its commands: `tick`, `run`, `pending`, `approve`, `reject`, `story`, `arc`, `context` |
| `dm_state.py` | The memory file (`state.db`): schema, upgrades, helpers |
| `world_query.py` | Read-only questions to the game databases |
| `factions.py` | Friend or foe, read from the server's extracted faction files |
| `write_quest.py` | One-off: have the model write a bounty for a character, without the story loop |
| `llm.py` | The only place that calls a model. Add a provider here to switch models |
| `hot_quest.py` | Checks a quest spec and renders it to SQL |
| `apply_quest.py` | Puts a quest spec live, retires it, or removes it |
| `console.py` | Client for the server's remote console, with the command allow-list |
| `env.example` | Template for `.env` |
| `test_quest.json` and the two `test_quest_*.sql` | A fixed quest for smoke-testing the pipeline without a model |
| `tests/test_offline.py` | Checks that need no server: `python3 -m unittest discover tests` |
| `server/` | Copies of custom-sql files from the game server (see step 1) |
| `docs/ai_dm_spec.md` | Design spec |
| `docs/dev_plan.md` | What is built and what is next |

Not in git: `.env` (secrets), `state.db` (the story), `issued/` (records from `write_quest.py`).

## Fresh-server setup

Do these in order. Paths assume the server lives in `~/cmangos-deploy`.

### 1. Game content (optional)

The DM needs nothing custom to run: it offers bounties through NPCs that are already in the world and pays in gold.

To let it hand out capstone spell books as rewards, copy `server/spell_books.sql` into `~/cmangos-deploy/storage/classic/database/custom-sql/`. It is a generated file; do not edit it by hand. `server/npc_spawns.sql` places the book vendors that file defines, and is not needed by the DM.

### 2. Turn on the remote console

In `~/cmangos-deploy/config/classic/mangosd.conf`:

```
SOAP.Enabled = 1
```

In `~/cmangos-deploy/compose.yaml`, under the `mangosd` service's `ports`:

```yaml
      - 127.0.0.1:7878:7878
```

The `127.0.0.1` prefix keeps the console reachable from the host only. Without it the port is open to the whole network.

Then restart:

```
cd ~/cmangos-deploy
docker compose down && docker compose up -d
```

### 3. Create the DM's account

The console needs a game account at level 3. Name and password are 16 characters at most.

```
docker compose attach mangosd
account create OVERSEER <password>
account set gmlevel OVERSEER 3
```

Detach with Ctrl+P then Ctrl+Q.

### 4. Configure

```
cp env.example .env
```

Edit `.env` and set at least `DM_SOAP_PASS` and `ANTHROPIC_API_KEY`. If the server is not in `~/cmangos-deploy`, set `DM_COMPOSE_DIR`.

### 5. Smoke tests

Run these in order. Each one tests one more link in the chain.

```
python3 -m unittest discover tests               # the rules, with no server involved
python3 factions.py                              # the faction files are readable and make sense
python3 console.py "server info"                 # the remote console answers
python3 world_query.py <Character>               # the database is readable
python3 apply_quest.py test_quest.json           # a fixed quest goes live
python3 apply_quest.py test_quest.json --remove  # and comes down again
python3 write_quest.py <Character> --dry-run     # the model writes a quest; nothing applied
```

`factions.py` prints how seven well-known factions treat a Human and an Orc, with the answers to expect. `world_query.py` lists the nearby NPCs the DM could speak through. The fixed test quest is offered by Marshal McBride in Northshire, so it needs an Alliance character to take it.

### 6. Run it

With a character online:

```
python3 dm.py tick        # notices the character and writes an arc
python3 dm.py pending
python3 dm.py approve 1   # the arc
python3 dm.py tick        # now writes the first bounty
python3 dm.py approve 2
```

To keep it watching:

```
python3 dm.py run --every 300
```

## Commands

### The loop

| Command | Effect |
|---|---|
| `dm.py tick` | One pass: observe, record, maybe propose |
| `dm.py run --every 300` | Tick every 300 seconds until stopped |
| `dm.py pending` | Show proposals waiting for a decision: arcs and bounties |
| `dm.py approve <n>` | Put proposal `n` into effect |
| `dm.py reject <n> "reason"` | Discard it. The reason is passed to the model next time |
| `dm.py story <Character>` | The chronicle: story so far, bounties and how each ended, events |
| `dm.py arc <Character>` | The arc in force and the ones that have ended. A spoiler if you play that character |
| `dm.py arc <Character> --seed "..."` | Have a new arc written around your direction |
| `dm.py context <Character>` | Exactly what the model would be told next. No model call |

A tick does three things without asking: it forces a save, it stops offering a bounty once its character has turned it in, and it calls the model when a character has no bounty out.

### One-off tools

| Command | Effect |
|---|---|
| `write_quest.py <Character> [--hint "..."]` | Model writes one bounty; asks before applying. `--dry-run` applies nothing; `--show-context` skips the model call |
| `apply_quest.py <spec.json> [--announce "..."]` | Put a spec live |
| `apply_quest.py <spec.json> --retire` | Stop offering it; keep the quest and everyone's history |
| `apply_quest.py <spec.json> --remove` | Delete it and every character's record of it |
| `apply_quest.py <spec.json> --dry-run` | Print the SQL and console commands; change nothing |
| `console.py "<command>"` | Send one allowed GM command |
| `world_query.py <Character>` | Print what the DM can see for a character |

## Settings

All settings are read from the environment, or from `.env` in this folder.

| Setting | Default | Meaning |
|---|---|---|
| `DM_SOAP_URL` | `http://127.0.0.1:7878/` | The server's remote console |
| `DM_SOAP_USER` | none | Console account name |
| `DM_SOAP_PASS` | none | Console account password |
| `DM_COMPOSE_DIR` | `~/cmangos-deploy` | Folder holding `compose.yaml` |
| `ANTHROPIC_API_KEY` | none | Claude API key |
| `DM_MODEL` | `claude-sonnet-5-5` | Model that writes quests |
| `DM_DBC_DIR` | inside `DM_COMPOSE_DIR` | Folder of the server's extracted `.dbc` files |
| `DM_COOLDOWN_MINUTES` | `20` | Wait after a bounty ends before proposing the next |
| `DM_STALE_HOURS` | `24` | An offered bounty nobody accepts is dropped after this |
| `DM_MAX_PROPOSALS_HOUR` | `6` | Ceiling on model calls per hour |
| `DM_AUTO_APPROVE` | `letter` | Proposal types that skip review: `bounty`, `arc`, `letter` |
| `DM_IGNORE_CHARACTERS` | empty | Names the DM should not track |
| `DM_MAIL_CHARACTER` | empty | A character the DM owns; mail to it is read as letters to the Overseer |
| `DM_STATE` | `state.db` in this folder | Path to the memory file |

For testing against something other than the live server: `DM_DB_COMMAND`, `DM_DB_QUERY_COMMAND`, `DM_WORLD_DB`, `DM_CHAR_DB`, `DM_LLM_PROVIDER` (`anthropic` or `stub`), `DM_LLM_STUB`, `DM_ANTHROPIC_URL`.

## Data and backups

- **`state.db` is the story.** It holds every chronicle, bounty and summary, and nothing can rebuild it. Back it up; it is deliberately not in git.
- **`.env` holds an admin password and an API key.** Never commit it.
- **Live quests exist only in the game database.** A world database rebuild removes them. `state.db` still has each quest's full spec, but nothing re-applies them automatically yet.

## Safety model

- **Allow-list.** The console account is a full admin, and the server runs any command it is sent. `console.py` sends only twelve command types and refuses everything else before it leaves the machine.
- **The model never writes SQL.** It fills in a small form. The script sets the quest id, giver, zone, levels and XP, and rejects any choice that is not on the lists it was given.
- **Preflight.** Nothing is written if the giver, target or reward is missing.
- **Reserved ids.** DM quests use ids 30000 to 39999, so they are easy to find and remove.
- **Approval.** By default a quest reaches the game only through `approve` or an explicit `apply_quest.py` run. With `bounty` in `DM_AUTO_APPROVE`, the checks above are the only gate.

Known gap: the scripts reach the database as its root user through the container. A dedicated user with narrow permissions is still to do.

## Known limits

- **Heralds are existing quest NPCs.** A bounty is offered through a living NPC that already gives quests, has a single spawn, and is not hostile to the character. The search widens from 300 yards until it finds some. An NPC that is not a quest giver cannot be used without a server restart.
- **Bounties are visible to everyone** at the giver, not only the character they were written for. The chronicle records who actually completed each one.
- **Quest markers are not pushed.** A player already standing near the giver will not see the "!" for a new quest until the NPC comes back into view. Every bounty is announced for this reason.
- **A tick is a sample.** Company during a hunt is whoever was grouped or nearby when a tick ran. The server does not record who landed a kill.
- **Friend or foe comes from the game's own faction files.** If they cannot be read, the DM falls back to rough rules: only "friendly to all" NPCs as heralds, and city factions excluded as targets.
- **Zone names** come from a built-in table of about 45 zones. Others show as "zone 123".

## Troubleshooting

| Message | Likely cause |
|---|---|
| `Unavailable: cannot reach http://127.0.0.1:7878/` | Remote console off, port not mapped, or server down (step 2) |
| `AuthFailed: server rejected the account name or password` | Wrong `DM_SOAP_USER` or `DM_SOAP_PASS` |
| `AuthFailed: account level is too low` | The account is not level 3 (step 3) |
| `no living, friendly quest giver was found anywhere on this character's map` | The faction files could not be read (run `factions.py`), or the character is inside an instance |
| `factions: cannot read the faction files` | `DM_DBC_DIR` or `DM_COMPOSE_DIR` points at the wrong place |
| `no suitable creatures alive near this character` | Nothing at the right level within 400 yards, or the area is hunted out under long respawns |
| `preflight found a problem; nothing was written` | The spec points at a creature or item that does not exist on this server |
| `Claude API returned HTTP 401` | Missing or wrong `ANTHROPIC_API_KEY` |
| `the model replied without calling the tool` | The model answered in prose. Run it again |
