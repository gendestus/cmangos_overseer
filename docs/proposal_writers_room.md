# Proposal: writers' rooms for the Overseer's plans

Status: implemented 2026-10-12 in `room.py` and `writers/`.
Decisions taken (section 6): campaigns and chapters get rooms as well as arcs, because testing showed the strategy produces more fulfilling stories; every member of a room runs on the model its stage already used; bounties keep a single call.
Audience: whoever changes it (currently Claude Code).
Reference: `story_harness.zip` (repo root, not in git), a standalone proof of concept: three one-sided writers draft a mini-arc, a head writer weaves them into one throughline and picks the reward.

## 1. Problem

Each plan (campaign, chapter, arc) was written by one model call. One writer hedges: it balances success, cost and surprise inside a single draft, and the result is often the safe middle. The harness shows a better shape: writers who each commit to one side, and an editor who picks a spine and grafts.

The harness also keeps its writers as data: a config file names each writer, its stance, its prompt files, model and token limit. Changing the room means editing that file or a prompt, never Python. AIDM's prompts were string constants in `dm.py`.

## 2. Decision

Campaigns, chapters and arcs are written by a room. `propose_campaign`, `propose_chapter` and `propose_arc` keep their signatures and still store one proposal each; inside, `room.write` runs the writers in parallel and then the weaver, whose answer goes through the stage's validator exactly as before. Approval, the plan tables, bounties and everything downstream are unchanged.

```
champion  ─┐
tormentor ─┼─> weaver ─> validate_campaign / validate_chapter / validate_arc ─> proposal
trickster ─┘
```

The rooms live in `writers/`, not in Python:

```
writers/
  campaign.json  chapter.json  arc.json      one roster per stage
  shared.md                                  what every member knows: the server, the three layers, bounty mechanics
  campaign.md  chapter.md  arc.md            the rules for each plan, moved out of dm.py word for word
  writer.md                                  what every writer is told: commit to your side, leave rewards alone
  champion.md  tormentor.md  trickster.md    one file per stance, from the harness
  weaver.md                                  the head writer's method: spine, grafts, cuts
```

Adding a fourth writer is a new `.md` file and one block in a roster. An empty `writers` list leaves the weaver alone, which is the old single call with the weaver's method on top. `DM_WRITERS_ROOM=off` does the same for every stage.

What stays in Python, on purpose: the tool schemas and the validators. They are the contract between the model and `state.db`; a field asked for in a prompt without a validator would be silently dropped, so the shape of the answer is not something to edit from a text file. The prompts may say anything; the schema says what is kept. For the same reason the closing line, "Respond by calling the X tool exactly once", is added by `room.py`: the tool's name is Python's.

## 3. Design

### 3.1 The rosters

Same shape as the harness's `config.json`; paths are relative to the folder, and `model` may be omitted.

```json
{
  "writers": [
    {"name": "champion", "stance": "wants the character and their faction to succeed",
     "prompts": ["shared.md", "arc.md", "writer.md", "champion.md"], "max_tokens": 4096}
  ],
  "weaver": {"name": "weaver", "prompts": ["shared.md", "arc.md", "weaver.md"], "max_tokens": 6000}
}
```

- `model` omitted means the model the stage always used: `DM_PLAN_MODEL` for campaigns and chapters, `DM_MODEL` for arcs. Naming one in the roster pins that member.
- `room.load` checks a roster: names unique, a stance for every writer, every prompt file readable and non-empty, `max_tokens` a positive whole number, no unknown keys (a typo like `max_token` is an error, not a default). Errors name the file and the member.
- Rosters are read on every plan, so an edit takes effect without restarting `dm.py run`.
- `dm.py room` (or `python3 room.py`) checks all three and lists their members.

### 3.2 The writers

Each writer gets the same user message the single call got (`campaign_message`, `chapter_message`, `arc_message`), plus "Write your draft for this character." Its tool is the stage's own tool renamed `draft_<stage>`, with the fields only the weaver decides taken out (`DRAFT_OMITS` in `dm.py`): the log note and established facts everywhere, and for an arc also the signature reward and the previous arc's outcome. Drafts are not validated; they are material for the weaver.

Writers run in parallel with a `ThreadPoolExecutor` over `llm.ask_for_tool_call`, which is stdlib `urllib`, so nothing new is installed. The harness's `anthropic` SDK and structured outputs were not brought in. All database work stays on the calling thread.

### 3.3 The weaver

The weaver's message is the same context, then each draft labelled with its writer and stance, as in the harness, then "Write the plan that will be played." Its tool is the stage's own plus two required fields: `spine` (the writer whose draft it built on) and `notes` (per writer, what was kept and what was cut). The validators ignore both.

The harness tracked a source per beat (`drawn_from`). Campaign acts, chapter seeds and arc beats are three different shapes, so the room records one spine per plan instead and leaves the detail to the notes.

### 3.4 What the proposal keeps

The proposal's payload holds the plan as before plus `room`: the spine, the notes, each draft, and each member's model, tokens and whether its draft was reused. `tokens_in`/`tokens_out` on the proposal are the room's totals and `model` is the weaver's. The hourly ceiling counts proposals, so one room is one against it.

`dm.py pending` shows the spine and the notes; `dm.py room <n>` shows the drafts and each member's cost, sealed types needing `--reveal`.

### 3.5 Reusing drafts

A writer's draft is kept in `state.db` (`room_drafts`) under a hash of the provider, model, token limit, system prompt, message and tool. A draft whose key is there is not asked for again, so when the weaver's answer is rejected, or one writer's call fails, the next attempt with the same context calls only what is missing. Drafts older than a week are deleted.

Left out of the harness: the file lock (one `dm.py run` per `state.db` is already enforced), `--take` (a rejected plan's reason goes into the next message, which changes every key), and the `runs/` folder (the proposal is the record). Across ticks the message often changes (a new chronicle event), which changes every key; that is correct, since the drafts were written without it.

## 4. Cost

A plan goes from one call to four. Campaigns are written once per character, chapters once per zone and arcs every four to eight levels, so this is a few calls a level, not a few a tick. Every plan proposal carries the room's token totals.

## 5. Tests

`tests/test_offline.py`, class `WritersRoom`: the shipped rooms load; writers draft and the weaver writes; a retry calls only the weaver; a failed writer fails the room and keeps the other drafts; the room switched off is one call; the drafts can be read back; each kind of bad roster is refused naming the member. `Chapters` runs real ticks with rooms on through the stub provider. The rest of the suite runs with `DM_WRITERS_ROOM=off`, so its stubs and call sequences are unchanged.

## 6. Open questions, as decided

1. **Only arcs, or campaigns and chapters too?** All three.
2. **Writers on a cheaper model?** No: every member on its stage's model. A roster can still pin one.
3. **Rooms for bounties?** No. Bounties are frequent and the room's value is in the plan, not the errand. Act reviews keep a single call too.
