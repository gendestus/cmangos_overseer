# The Overseer: an AI dungeon master for CMaNGOS Classic

A small service that watches the players on a private CMaNGOS Classic (1.12.1) server, remembers what they do, and writes bounty quests that continue each character's story. Quests go live in the running game with no restart and no client patch.

It runs beside a [cmangos-deploy](https://github.com/mserajnik/cmangos-deploy) server and is built for one to three players. Design background is in `docs/ai_dm_spec.md`.

## How it works

1. **Observe.** A tick forces a save and reads the game databases: who is online, where, what they finished, who they are grouped with.
2. **Remember.** Anything new goes into the DM's own memory file, `state.db`: a chronicle per character, every bounty and how it ended, and a running story summary.
3. **Plan.** The first time a character is noticed, the model writes a private mini-arc for them: what the Overseer wants them to become over the next few levels, the enemy the story turns toward, and two to four beats. When an arc ends, the next is planned from how it actually went, so the story can turn.
4. **Propose.** When a character has no bounty out, the model is given the arc, the story so far, and real NPCs, creatures and rewards. Creatures come with their rank, how many are alive, and the distance and compass bearing of the nearest one, in two tiers: nearby, and far enough to be worth a journey. A named elite is offered only to a character with company or a wide level margin, and the last two bounties' targets are left out. It picks a **kind** of bounty, which NPC the Overseer speaks through, and one or two objectives; the proposal is stored, not applied.
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
| `console.py` | Client for the server's remote console, with the command allow-list and the kill switch |
| `db_users.py` | Creates the two narrow database users and proves what each may do |
| `env.example` | Template for `.env` |
| `tests/test_quest.json` and the two `tests/test_quest_*.sql` | A fixed quest for smoke-testing the pipeline without a model |
| `tests/test_offline.py` | Checks that need no server: `python3 -m unittest discover tests` |
| `server/` | custom-sql files for the game server: the spell books (step 1) and the DM's story props (step 6) |
| `docs/ai_dm_spec.md` | Design spec |
| `docs/DEV_PLAN.md` | What is built and what is next |

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

### 5. Narrow database users

Out of the box the scripts would reach the database as its root user. Make two users that cannot do more than they need:

```
python3 db_users.py --create --write-env
```

This creates `dm_read`, which can only read, and `dm_write`, which can only insert into and delete from the three tables a bounty lives in, plus the players' record of it. It writes their passwords into `.env` (keeping a copy of the old file as `.env.bak`) and then proves the grants: the reader is refused a write, and the writer is refused the accounts database, a player's mail, and any change to a creature or an item.

`python3 db_users.py` shows what exists; `--check` re-runs the proofs; `--drop` removes both users, after which the scripts fall back to root.

A later phase that writes to a new table needs a line added to `WRITE_GRANTS` in `db_users.py` and a re-run of `--create`. An offline test fails if the renderer writes to a table the writer has no grant for.

### 6. Story props, for trophy bounties (needs one restart)

Trophy bounties need the DM's pool of story props. New item types load only at server start, so this is the one step that needs a restart.

```
cp server/dm_props.sql ~/cmangos-deploy/storage/classic/database/custom-sql/
docker compose restart mangosd      # from the server folder
```

The file is idempotent, so leaving it in `custom-sql` is correct: it is re-applied on every start and rewrites only the ids it owns. Check it took:

```
python3 -c "import world_query; print(len(world_query.props()), 'props')"
```

Then confirm one in game with `.additem 200000` — a Stolen Book, with a book icon. Without this step the DM simply never offers a `trophy` bounty; every other kind works.

### 7. Smoke tests

Run these in order. Each one tests one more link in the chain.

```
python3 -m unittest discover tests               # the rules, with no server involved
python3 db_users.py --check                      # each database user can do its job and no more
python3 -c "import world_query as w; print(len(w.props()))"   # the story props are installed
python3 factions.py                              # the faction files are readable and make sense
python3 console.py "server info"                 # the remote console answers
python3 world_query.py <Character>               # the database is readable
python3 apply_quest.py tests/test_quest.json     # a fixed quest goes live
python3 apply_quest.py tests/test_quest.json --remove   # and comes down again
python3 write_quest.py <Character> --dry-run     # the model writes a quest; nothing applied
```

`factions.py` prints how seven well-known factions treat a Human and an Orc, with the answers to expect. `world_query.py` lists the nearby NPCs the DM could speak through. The fixed test quest is offered by Marshal McBride in Northshire, so it needs an Alliance character to take it.

### 8. Run it

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
| `dm.py pause "why"` | Stop everything that reaches the game. Reading still works |
| `dm.py resume` | Undo it |
| `dm.py purge` | Take every DM quest back out of the game, after confirmation |

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

## Kinds of bounty

The model picks a kind, and each one is held to its own rules. They are defined in one table, `KINDS` in `write_quest.py`, which is also what the prompt and the validator read, so the rules the model is told and the rules it is held to cannot drift apart.

| Kind | What it asks for | Held to |
|---|---|---|
| `hunt` | Kill several of one ordinary creature | An ordinary creature only, one objective, up to 12 |
| `mark` | One named creature, killed once | A rare, elite or rare elite, exactly one kill |
| `journey` | A hunt or mark far enough away to be worth the walk | At least one target in the far tier |
| `party` | Two objectives, written for a group | The character has online company; exactly two objectives |
| `trophy` | Collect something the creature is carrying | The prop is one the DM owns, is not in use, and the character does not already hold; the kills it implies are within what is alive and under 24 |

Money scales with the kind: a `mark` or a `party` bounty may pay twice a `hunt`, a `journey` or a `trophy` half again. Kill counts are capped by the kind and by how many of that creature are actually alive.

Each objective also carries a short label for the quest log, so a two-objective bounty does not show two unnamed counters, and a `party` bounty sets the quest's "Suggested players" line.

An elite offered on the strength of a party, and any `party` bounty, is **checked again at approval**: if the company has gone, `approve` refuses and the next tick writes a different bounty.

`trial` (a hunt against a timer) is still to come; it needs a spike in the game client first.

### Trophies

A trophy bounty does not look for something a creature already drops. The DM owns a pool of about a hundred generic story props — `Stolen Book`, `Guttered Candle`, `Severed Paw` — and when a bounty needs one it is **added to that creature's loot live**, as a quest-only drop, then deleted when the bounty ends. That is what lets the Overseer say *the kobolds have been carrying off my books* and have it be true.

The design and the evidence behind it are in `docs/proposal_prop_trophies.md`. What matters in use:

- A quest-only drop appears **only** for a character whose bounty needs it, and stops once they have enough. Nobody else's loot is touched.
- Props are flagged as party loot, so each member of a group takes their own copy from one corpse rather than competing.
- The loot row is tagged `dm:<quest id>`, and nothing untagged is ever written or deleted. Retiring or removing a bounty takes its props out; `dm.py purge` takes out every DM prop anywhere.
- The prop pool itself survives a purge. It is permanent; only the loot rows come and go.
- **A purge cannot empty a player's bags.** Loot tables are restored exactly, but anything already looted stays looted. The DM therefore never offers a prop the character is already carrying.

Installing the pool needs one restart, because new item types load only at server start. See setup step 6.

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
| `DM_GEAR_EVERY` | `2` | At most one gear reward in this many consecutive bounties |
| `DM_PRIZE_LEVEL_SPAN` | `4` | Levels a character must gain between rare prizes |
| `DM_PRIZE_REACH` | `5` | How many levels above the character a prize may be |
| `DM_AUTO_APPROVE` | `letter` | Proposal types that skip review: `bounty`, `arc`, `letter` |
| `DM_CHARACTERS` | empty | If set, the only characters the DM notices. Needed on a playerbot realm |
| `DM_IGNORE_CHARACTERS` | empty | Names the DM should not track. Applied after `DM_CHARACTERS` |
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
- **Narrow database users.** Reads go through a user that cannot write; writes through a user that can only touch `quest_template`, the two giver link tables and `character_queststatus`. It cannot read a player's mail, reach the account database, or change a creature or an item. See setup step 5.
- **A kill switch.** `dm.py pause "why"` stops every path to the game: no tick, no model call, no quest written, no console command, whichever script is run. Reading is unaffected, so the chronicle still reads while it is off. `DM_PAUSE=1` does the same from the environment.
- **One command undoes everything.** `dm.py purge` lists every quest in the DM's id range that is in the world database, asks for confirmation, then deletes each one, its giver links and every character's record of it. It reads the live database rather than the DM's memory, so a quest whose record was lost is still cleaned up. The chronicle is kept.

## Known limits

- **Heralds are existing quest NPCs.** A bounty is offered through a living NPC that already gives quests, has a single spawn, and is not hostile to the character. The search widens from 300 yards until it finds some. An NPC that is not a quest giver cannot be used without a server restart.
- **Bounties are visible to everyone** at the giver, not only the character they were written for. The chronicle records who actually completed each one.
- **A party can disband after a bounty is written.** An elite is offered on the strength of who was online and grouped at that moment. Nothing re-checks it at turn-in.
- **Quest markers are not pushed.** A player already standing near the giver will not see the "!" for a new quest until the NPC comes back into view. Every bounty is announced for this reason.
- **A tick is a sample.** Company during a hunt is whoever was grouped or nearby when a tick ran. The server does not record who landed a kill.
- **Friend or foe comes from the game's own faction files.** If they cannot be read, the DM falls back to rough rules: only "friendly to all" NPCs as heralds, and city factions excluded as targets.
- **Zone names** come from a built-in table of about 45 zones. Others show as "zone 123".
- **Playerbots must be excluded by hand.** A realm with `AiPlayerbot.Enabled = 1` can hold a thousand characters. The DM cannot tell a bot from a player — the account names that would say so live in the `realmd` database, which its read-only user is deliberately denied — so set `DM_CHARACTERS` to the real players. Without it, the first tick after bots log in starts writing story arcs for them.
- **A chronicle is keyed to a character's guid, not its name.** Delete a character and make another with the same name and the new one starts a fresh story, which is usually what you want. `dm.py story` then says how many have held the name and shows whoever holds it now.

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
