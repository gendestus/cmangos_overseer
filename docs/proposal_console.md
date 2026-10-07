# Proposal: `dm.py run` as a console

Status: proposed 2026-10-07. A usability change; the DM's behaviour is unchanged.
Audience: whoever implements it (currently Claude Code).
Reference: `console_loop_prototype.py` beside this file is a working model of the mechanics, with the DM replaced by stand-ins. `console_loop_drive.py` types at it through a pseudo-terminal and is how it was tested.

## 1. Problem

`python3 dm.py run` loops and prints. To approve a proposal, read a story or change anything, the owner stops it with Ctrl+C, runs another command, and starts it again.

## 2. Decision

When `run` is started from a terminal, ticks happen in a background thread and the foreground is a prompt:

```
ticking every 300 seconds in the background. Type help, or quit to leave.
overseer> pending
...
overseer> approve 3
quest 30012 is live for Gendestus; the story has moved on.
overseer> appro                      <- half-typed when a tick reports
tick: 2 character(s) online
  Gendestus: bounty proposal 4 written, "The Paymaster's Road"
overseer> appro                      <- the line is still there
```

Every existing command works at the prompt exactly as it does after `python3 dm.py`. A handful of commands exist only in the console.

Two things stay as they are:

- **Every command still works from another shell.** The state lives in `state.db` and the game databases, not in the running process. That is already true today, and is the workaround until this is built.
- **Without a terminal there is no console.** Under a service, a pipe or `nohup`, `run` is the plain loop it is now. `--no-console` forces that.

## 3. Design

### 3.1 The four mechanics

The prototype exists to settle these. Each is marked in its source.

1. **Tick output must not wreck the line being typed.** Background output goes through one `emit()` that clears the current line, prints, then redraws the prompt and whatever was typed so far (`readline.get_line_buffer()`). The "am I at the prompt" flag is set from `readline.set_pre_input_hook`, which runs after readline has drawn the prompt; setting it earlier produced a doubled prompt in testing.
2. **A tick and a command never run together.** One lock, held by whichever is touching the DM. A command typed during a tick prints "waiting for the tick to finish..." and then runs.
3. **A mistake does not end the session.** Both argparse and the `cmd_*` functions end with `sys.exit`. The console catches `SystemExit` around parsing and around the call, and prints the message.
4. **Leaving waits.** `quit` or Ctrl+D sets a stop flag and joins the ticker thread, so a tick half-way through writing a bounty finishes first. Ctrl+C at the prompt only cancels the line.

### 3.2 Changes to `dm.py`

- Move the parser out of `main()` into `build_parser()`, so the console can reuse it. Record the command names on it.
- `cmd_run` chooses: console when `sys.stdin.isatty()` and not `--no-console`; otherwise today's loop.
- The ticker thread calls `tick(db, say=emit)`. `tick` already takes `say`.
- **One database connection per thread.** `sqlite3` refuses a connection used from another thread, so the ticker opens its own with `dm_state.connect()`. The prompt keeps the one `main()` opened.
- In `dm_state.connect()`, set `PRAGMA busy_timeout = 5000`, so the two connections, and any `dm.py` command run from another shell, wait for each other instead of failing.
- **A catch-all for stray prints.** Anything the ticker thread prints with a bare `print` would still break the prompt. In console mode, wrap `sys.stdout` so writes from a non-main thread are sent through `emit()` a line at a time.

### 3.3 Commands only the console has

| Command | Effect |
|---|---|
| `tick` | Run a tick now, without waiting for the timer |
| `every <seconds>` | Change the interval. Minimum 30 |
| `status` | Ticks so far, how the last one went, time to the next, whether paused, proposals waiting |
| `quiet` / `loud` | Quiet shows one line per tick, plus anything that needs you. Loud shows every line, as now |
| `log [n]` | The last `n` lines the ticks printed (default 40), from a buffer of 500 |
| `help` | The console commands, then the usual command list |
| `quit`, `exit`, Ctrl+D | Finish the tick in progress and leave |

`run` typed at the prompt answers "already running".

### 3.4 Conveniences

- **Prompt shows state:** `overseer>` normally, `overseer (paused)>` when the kill switch is on, `overseer (2 waiting)>` when proposals need a decision.
- **Short forms:** `p` for `pending`, `a 3` for `approve 3`, `r 3 "why"` for `reject`.
- **History** across sessions in `.dm_history`, which goes in `.gitignore`.
- **Tab completion** for command names, known character names from `state.db`, and pending proposal numbers.
- **A bell** when a tick leaves something waiting for a decision, if `DM_BELL=1`.

### 3.5 What does not change

- `tick`, `approve` and the rest are not rewritten. The console is a front end.
- The pause flag file works as now, from the prompt or from another shell.
- Sealed plans stay sealed at the prompt; `--reveal` works the same way.
- Commands that ask a question, such as `purge`, keep their own prompt. The lock means no tick output can arrive while one is waiting for an answer.

## 4. Limits

- **A long command delays ticks,** and a long tick delays commands. A tick that calls the model can take half a minute. That is the price of never running the two together, and it is the right trade.
- **Line redraw needs a normal terminal** and Python's `readline`, which the server's Linux has. Where `readline` is missing, tick output is printed plainly and may land in the middle of a typed line.
- **One console per `state.db`.** Starting a second `run` would tick twice as often. Take a lock file on start and refuse if another `run` holds it.

## 5. Tests to add

- **Dispatch, offline:** an unknown command, an argparse error and a `sys.exit` from a command each leave the session running; `run` is refused; short forms resolve.
- **Scheduling, offline:** `every` moves the next tick; `tick` forces one; a failing tick is reported and the loop continues.
- **End to end, through a pseudo-terminal:** adapt `console_loop_drive.py` to start `dm.py run` with the stub model. It should type a command during a tick, leave a line half-typed across a tick and finish it, and quit during a tick. Pass when the exit status is 0, no prompt is doubled, and the half-typed line survived.

The prototype passes that last test as it stands, three runs out of three.

## 6. Done when

- `python3 dm.py run` shows a prompt, and proposals can be read and approved without stopping it.
- A tick that reports while a line is half-typed leaves the line intact.
- `quit` during a tick waits for it.
- Started under `nohup` or a service, `run` behaves exactly as it does today.
- A second `run` against the same `state.db` is refused.

## 7. Decisions for the owner

1. **Quiet or loud by default.** Proposed: loud, as now, with `quiet` a keystroke away.
2. **Minimum interval.** Proposed: 30 seconds, since every tick forces a server save.
3. **Should `approve` with no number approve the only waiting proposal?** Proposed: yes when exactly one is waiting, otherwise list them.
