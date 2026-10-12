# Proposal: a writers' room for arcs

Status: proposed 2026-10-12. Not built.
Audience: whoever implements it (currently Claude Code).
Reference: `story_harness.zip` (repo root, not in git), a standalone proof of concept: three one-sided writers draft a mini-arc, a head writer weaves them into one throughline and picks the reward.

## 1. Problem

An arc is written by one model call (`propose_arc` in `dm.py`, prompt `ARC_SYSTEM`). One writer hedges: it balances success, cost and surprise inside a single draft, and the result is often the safe middle. The harness shows a better shape: writers who each commit to one side, and an editor who picks a spine and grafts.

The harness also keeps its writers as data: a config file names each writer, its stance, its prompt files, model and token limit. Changing the room means editing that file or a prompt, never Python. AIDM's prompts today are all string constants in `dm.py`.

## 2. Decision

Arcs are written by a room. `propose_arc` keeps its signature and still stores one `arc` proposal; inside, it runs the writers in parallel and then the head writer, whose answer goes through `validate_arc` exactly as today. Approval, the arc table, bounties and everything downstream are unchanged.

```
champion  ─┐
tormentor ─┼─> weaver ─> validate_arc ─> proposal (type 'arc')
trickster ─┘
```

The room lives in a folder, not in Python:

```
writers/
  room.json          the roster: writers, the weaver, models, token limits
  shared.md          what every member knows: the server, mini-arcs, bounty mechanics
  writer.md          what every writer is told: commit to your side, no rewards
  champion.md        one file per stance
  tormentor.md
  trickster.md
  weaver.md          the head writer's method: spine, grafts, cuts
  arc_rules.md       the arc's field rules, moved out of ARC_SYSTEM
```

Adding a fourth writer is a new `.md` file and one block in `room.json`. Removing all writers leaves the weaver alone with `arc_rules.md`, which is today's single call.

What stays in Python, on purpose: the tool schemas and `validate_arc`. They are the contract between the model and `state.db`; a field added in a prompt without a validator would be silently dropped, so the shape of the answer is not something to edit from a text file. The prompts may say anything; the schema says what is kept.

## 3. Design

### 3.1 `room.json`

Same shape as the harness's `config.json`, with two changes: paths are relative to `writers/`, and `model` may be omitted.

```json
{
  "writers": [
    {"name": "champion",  "stance": "wants the character and their faction to succeed",
     "prompts": ["shared.md", "writer.md", "champion.md"], "max_tokens": 3000},
    {"name": "tormentor", "stance": "wants the character and their faction to suffer",
     "prompts": ["shared.md", "writer.md", "tormentor.md"], "max_tokens": 3000},
    {"name": "trickster", "stance": "wants a dramatic twist wherever possible",
     "prompts": ["shared.md", "writer.md", "trickster.md"], "max_tokens": 3000}
  ],
  "weaver": {"prompts": ["shared.md", "weaver.md", "arc_rules.md"], "max_tokens": 6000}
}
```

- `model` omitted means `DM_PLAN_MODEL`, then `DM_MODEL`, as for the other planning calls. Naming one in the file pins that member.
- A new module, `room.py`, loads and checks it: names unique and not `new`, every prompt file exists and is non-empty, `max_tokens` a positive integer, no unknown keys (a typo like `max_token` is an error, not a default). Errors name the file and the member.
- `DM_WRITERS_ROOM` in `.env` points at a different folder; `off` skips the writers and runs the weaver alone. Default `writers/`.
- The file is read on every arc, so an edit takes effect on the next arc without restarting `dm.py run`.

### 3.2 The writers

Each writer gets the same user message that `arc_message` builds today (character, campaign, chapter, seed, dossier, earlier arcs, chronicle, canon, books, the owner's direction), with one line changed at the end: "Write your draft for this character." Each calls a `submit_draft` tool:

| Field | |
|---|---|
| `premise`, `lure`, `adversary` | As in an arc, from this writer's side |
| `beats` | 2 to 4, each `level_band`, `intent`, `bounty_hook` |
| `ending` | How this version ends |
| `next_arc_hook` | The door it leaves open |

`bounty_hook` replaces the harness's `quest_hook`: one sentence the Overseer could turn into a bounty, which in AIDM means kill or collect a trophy through a herald, not an escort. `shared.md` is rewritten to say so, and to use AIDM's numbers (2 to 4 beats, bands within `ARC_REACH` levels, the adversary from the dossier when there is a chapter), not the harness's 3 to 5 beats.

Writers do not choose `signature_reward` or `adversary_creature`. Those are the weaver's, as loot is in the harness.

Writers run in parallel with a `ThreadPoolExecutor` over `llm.ask_for_tool_call`. It is stdlib `urllib`, so threads are fine; nothing new to install. The harness's `anthropic` SDK and structured outputs are not brought in.

### 3.3 The weaver

The weaver's message is the same context, then each draft labelled with its writer and stance (as the harness does), then the book list. It calls `submit_arc`: today's `ARC_TOOL` plus two optional fields:

- `beats[].drawn_from`: a writer's name or `new`.
- `notes`: per writer, what was kept and what was cut.

Optional so that `validate_arc` and every existing stub keep working. `validate_arc` gains a normaliser for `drawn_from` (unknown names become `new`, case folded) and keeps `notes` short.

`weaver.md` is the harness's text, with "three writers" made "the writers" so the roster can change without touching it, and "choose the reward by entry" replaced by "choose `signature_reward` from the books by name", which is what `validate_arc` already checks. `arc_rules.md` is `ARC_SYSTEM` moved verbatim, minus its first paragraph, which `shared.md` now covers.

### 3.4 What the proposal keeps

The proposal's payload stores the arc as today plus a `room` object: each writer's draft, the weaver's notes, each beat's `drawn_from`, and the model and token counts per member. The `proposals` row's `tokens_in`/`tokens_out` become the room's totals, so the hourly ceiling and cost reporting stay honest. The ceiling still counts proposals, so one room is one call against it, as one arc is today.

`show_proposal` for an arc shows each beat's source and the notes, and `--reveal` shows the drafts. `dm.py arc <Character> --reveal` shows where the arc in force came from.

### 3.5 Caching, from the harness, reduced

The harness caches each stage under a hash of everything that can change it, so a failed weaver does not re-pay the writers. AIDM needs the useful half of that:

- A table `room_stages (key, role, output, model, tokens_in, tokens_out, created_at)` in `state.db`. The key is a hash of the member's prompt text, model, `max_tokens`, schema and user message.
- A writer whose key is cached is not called. When `validate_arc` rejects the weaver, the next attempt with the same context re-runs only the weaver.
- Rows older than a week are deleted on the next arc.

What is left out: the file lock (one `dm.py run` per `state.db` is already enforced), `--take` (a rejected arc's reason already goes into the next message, which changes every key), and the `runs/` folder (the proposal is the record).

Caching only hits when the message is identical, which across ticks it often is not: a new chronicle event changes every key. That is correct (the drafts were written without it), and it is why this is a small table and not a bigger mechanism.

### 3.6 A command to see the room

`dm.py room` prints the roster: each member, its stance, its prompt files and model, and the check from 3.1. `dm.py room --check` exits non-zero on any error, for after an edit. `dm.py room <Character> --dry-run` prints the system prompt and message each member would get, with no model call.

## 4. Steps

Each is one commit.

1. **Prompts to files.** Create `writers/` with `arc_rules.md` (from `ARC_SYSTEM`) and `room.py` that loads a prompt list. `propose_arc` reads its system prompt from the file. No behaviour change; existing tests pass untouched.
2. **The roster.** `room.json`, the check in 3.1, `DM_WRITERS_ROOM`, `dm.py room` and `--check`. Tests: a good room loads; each kind of bad room fails with a message naming the member.
3. **The writers and the weaver.** `shared.md`, `writer.md`, the three stances and `weaver.md`, adapted as in 3.2 and 3.3. `submit_draft`, the two optional `submit_arc` fields, parallel drafting, totals on the proposal. Tests with a stub folder holding `submit_draft.json` and `submit_arc.json`; the test that asserts the tool-call sequence (`["submit_chapter", "submit_arc"]`) runs with `DM_WRITERS_ROOM=off`, and a new one asserts the room's sequence.
4. **Showing it.** Payload's `room` object, `show_proposal`, `arc --reveal`, `room <Character> --dry-run`.
5. **The stage cache.** `room_stages` table and its upgrade in `dm_state.py`, the cache check, the weekly purge. Test: a rejected weaver followed by a retry calls only the weaver.
6. **Docs.** README layout table and commands, `env.example`, a line in `DEV_PLAN.md` under phase 4, and this file's status.

Step 1 is worth having alone. Steps 2 and 3 are the feature. 4 to 6 can wait.

## 5. Cost

An arc goes from one planning call to four (three writers in parallel, then the weaver with three drafts in its input). Arcs are rare (one per four to eight levels per character), so this is a few cents per arc, not per tick. The cost is visible on every arc proposal's token totals.

## 6. Open questions

1. **Only arcs, or campaigns and chapters too?** This proposal does arcs. `room.json` could later hold a block per stage (`"arc": {...}, "chapter": {...}`), but nothing is built for that until arcs show the room is worth it.
2. **Writers on a cheaper model?** The default puts every member on the planning model. Putting the writers on `DM_MODEL` and only the weaver on `DM_PLAN_MODEL` would cut the cost roughly in half; it is a one-line change in `room.json` either way.
3. **Rooms for bounties?** No. Bounties are frequent and the room's value is in the plan, not the errand.
