"""The console `dm.py run` shows at a terminal: ticks in the background, a prompt in front.

    overseer> pending
    overseer> approve 3
    overseer (paused)> resume

Every dm.py command works at the prompt, typed without `python3 dm.py`. A few
exist only here: tick, every, status, quiet, loud, log, help, quit. The design
is docs/proposal_console.md; the four things that are easy to get wrong are
marked 1 to 4 below.

  1. Output from a tick must not wreck the line being typed.
  2. A tick and a command must never run at the same moment.
  3. A mistyped command must not end the session.
  4. Leaving must wait for a tick in progress.

Nothing here is needed for the DM to work: without a terminal, `run` is the
plain loop, and every command still works from another shell.
"""
import collections
import os
import shlex
import sys
import threading
import time

try:
    import readline                     # line editing and history; absent on some platforms
except ImportError:
    readline = None

import console
import dm_state

MIN_EVERY = 30                          # every tick forces a server save
LOG_LINES = 500
SHORT = {"p": "pending", "a": "approve", "r": "reject"}
BUILTINS = ("tick", "every", "status", "quiet", "loud", "log", "help", "quit", "exit")
NAMED = ("story", "arc", "campaign", "chapter", "context", "canon")      # commands that take a character
NUMBERED = ("approve", "reject")                              # commands that take a proposal
HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dm_history")
HELP = """console commands:
  tick               run a tick now
  every <seconds>    change the interval ({min} or more)
  status             ticks so far, the last one, the next one, what is waiting
  quiet / loud       one line per tick and anything that needs you / every line
  log [n]            the last n lines the ticks printed (default 40)
  help [command]     this, or one command's options
  quit               finish the tick in progress and leave (also exit, Ctrl+D)
short forms: p = pending, a 3 = approve 3, r 3 "why" = reject 3 "why"
every dm.py command works here, typed without 'python3 dm.py':"""


class ThreadRouted:
    """sys.stdout while the console runs: a bare print from the ticker goes through the console."""

    def __init__(self, owner, real):
        self.owner, self.real = owner, real
        self.partial = ""
        self.lock = threading.Lock()

    def write(self, text):
        if threading.current_thread() is threading.main_thread():
            return self.real.write(text)
        with self.lock:
            self.partial += text
            *lines, self.partial = self.partial.split("\n")
        for line in lines:
            self.owner.tick_say(line)
        return len(text)

    def flush(self):
        self.real.flush()

    def __getattr__(self, name):        # fileno, isatty, encoding: input() needs the real ones for readline
        return getattr(self.real, name)


class Console:
    def __init__(self, parser, db, tick, every, connect=dm_state.connect, out=None):
        self.parser, self.db, self.tick, self.connect = parser, db, tick, connect
        self.every = every
        self.real = out or sys.stdout
        self.work = threading.Lock()            # held by whoever is touching the DM: a tick or a command
        self.out = threading.Lock()             # one writer to the terminal at a time
        self.wake = threading.Event()           # "tick now", "the interval changed" or "stop"
        self.stopping = False
        self.forced = False
        self.prompting = False                  # the main thread is sitting in input()
        self.prompt = "overseer> "
        self.next_at = time.time()
        self.ticks = 0
        self.last = "none yet"
        self.loud = True
        self.log = collections.deque(maxlen=LOG_LINES)
        self.bell = os.environ.get("DM_BELL", "").strip() == "1"
        self.names, self.waiting_ids = [], []

    # -- 1. output that keeps the prompt intact ------------------------------------
    def emit(self, text=""):
        with self.out:
            if self.prompting and readline:
                typed = readline.get_line_buffer()
                self.real.write("\r\x1b[2K" + str(text) + "\n" + self.prompt + typed)
            else:
                self.real.write(str(text) + "\n")
            self.real.flush()

    def ring(self):
        if self.bell:
            with self.out:
                self.real.write("\a")
                self.real.flush()

    def tick_say(self, line=""):
        """What a tick says: always kept for `log`; shown in full when loud, trimmed when quiet."""
        line = str(line)
        self.log.append(line)
        if self.loud or line.startswith("tick") or any(word in line.lower() for word in ("fail", "could not")):
            self.emit(line)

    # -- the background ticker -----------------------------------------------------
    def waiting(self, db):
        return db.execute("SELECT id, type, name FROM proposals WHERE status = 'pending' ORDER BY id").fetchall()

    def run_one_tick(self, db):
        with self.work:                         # 2. never alongside a command
            started = time.time()
            before = {row["id"] for row in self.waiting(db)}
            try:
                self.tick(db, say=self.tick_say)
                db.commit()
                self.last = f"ok at {time.strftime('%H:%M:%S')}, {time.time() - started:.1f}s"
            except Exception as error:          # a failed tick must not end the loop
                db.rollback()
                self.last = f"failed at {time.strftime('%H:%M:%S')}: {error}"
                self.tick_say(f"tick failed: {error}")
            new = [row for row in self.waiting(db) if row["id"] not in before]
            if new:
                listed = ", ".join(f"{row['id']} ({row['type']} for {row['name']})" for row in new)
                self.emit(f"waiting for you: {listed}. Type pending.")
                self.ring()
            self.ticks += 1
        self.next_at = time.time() + self.every

    def due(self, now):
        return self.forced or now >= self.next_at

    def ticker(self):
        db = self.connect()                     # sqlite refuses a connection from another thread
        try:
            while not self.stopping:
                self.wake.wait(timeout=max(0.0, self.next_at - time.time()))
                self.wake.clear()
                if self.stopping:
                    break
                if not self.due(time.time()):
                    continue                    # woken only because the interval changed
                self.forced = False
                self.run_one_tick(db)
        finally:
            db.close()

    # -- commands that only make sense inside the console ---------------------------
    def set_every(self, seconds):
        self.every = seconds
        self.next_at = time.time() + seconds
        self.wake.set()

    def builtin(self, words):
        name = words[0].lower()
        if name in ("quit", "exit"):
            return "quit"
        if name == "help":
            if len(words) > 1:
                self.command_help(SHORT.get(words[1], words[1]))
            else:
                self.emit(HELP.format(min=MIN_EVERY))
                self.emit(self.parser.format_help())
        elif name == "tick":
            self.forced = True
            self.wake.set()
            self.emit("waiting for the tick in progress; another follows." if self.work.locked()
                      else "tick requested.")
        elif name == "every":
            if len(words) != 2 or not words[1].isdigit() or int(words[1]) < MIN_EVERY:
                self.emit(f"usage: every <seconds>, {MIN_EVERY} or more")
            else:
                self.set_every(int(words[1]))
                self.emit(f"ticking every {self.every} seconds; the next in {self.every}.")
        elif name == "status":
            self.emit(self.status())
        elif name in ("quiet", "loud"):
            self.loud = name == "loud"
            self.emit("loud: every line a tick prints." if self.loud
                      else "quiet: one line per tick, and anything that needs you. `log` shows the rest.")
        elif name == "log":
            if len(words) > 2 or (len(words) == 2 and not words[1].isdigit()):
                self.emit("usage: log [lines]")
            else:
                count = int(words[1]) if len(words) == 2 else 40
                lines = list(self.log)[-count:] if count else []
                self.emit("\n".join(lines) if lines else "nothing logged yet.")
        else:
            return None
        return "handled"

    def command_help(self, name):
        if name in BUILTINS:
            self.emit(HELP.format(min=MIN_EVERY))
        elif name not in self.parser.known:
            self.emit(f"no such command: {name}. Type help.")
        else:
            try:
                self.parser.parse_args([name, "--help"])
            except SystemExit:
                pass

    def status(self):
        busy = "a tick is running" if self.work.locked() else \
            f"next tick in {max(0, round(self.next_at - time.time()))}s"
        waiting = len(self.waiting(self.db))
        paused = console.paused()
        return (f"{self.ticks} tick(s) so far; last: {self.last}; {busy}; every {self.every}s; "
                f"{'paused (' + paused + ')' if paused else 'not paused'}; "
                f"{waiting} proposal(s) waiting; {'loud' if self.loud else 'quiet'}.")

    # -- one typed line ----------------------------------------------------------------
    def handle(self, line):
        """Run one typed line. False means leave."""
        try:
            words = shlex.split(line)
        except ValueError as error:
            self.emit(f"could not read that: {error}")
            return True
        if not words:
            return True
        words[0] = SHORT.get(words[0], words[0])
        outcome = self.builtin(words)
        if outcome == "quit":
            return False
        if outcome == "handled":
            return True
        if words[0] == "run":
            self.emit("already running.")
            return True
        if words[0] not in self.parser.known:
            self.emit(f"no such command: {words[0]}. Type help.")
            return True
        try:                                    # 3. argparse and sys.exit must not end the session
            args = self.parser.parse_args(words)
        except SystemExit:
            return True
        try:
            if self.work.locked():
                self.emit("waiting for the tick to finish...")
            with self.work:
                try:
                    args.run(self.db, args)
                finally:
                    self.db.rollback()          # as on the command line: what a command did not commit is gone
        except SystemExit as stop:
            if stop.code not in (None, 0) and not isinstance(stop.code, int):
                self.emit(stop.code)
        except KeyboardInterrupt:
            self.emit("\ninterrupted.")
        except Exception as error:
            self.emit(f"{words[0]} failed: {error}")
        finally:
            self.prompting = False              # a command's own input() ran the pre-input hook too
        return True

    # -- the prompt, its state and its completions ------------------------------------
    def refresh(self):
        """Prompt and completions, read on the main thread just before the prompt is drawn."""
        waiting = self.waiting(self.db)
        self.waiting_ids = [str(row["id"]) for row in waiting]
        self.names = [row["name"] for row in self.db.execute(
            "SELECT name FROM characters WHERE name IS NOT NULL ORDER BY name")]
        if console.paused():
            self.prompt = "overseer (paused)> "
        elif waiting:
            self.prompt = f"overseer ({len(waiting)} waiting)> "
        else:
            self.prompt = "overseer> "

    def candidates(self, before, text):
        """Completions for `text`, the word being typed, given the words `before` it."""
        words = before.split()
        commands = sorted((set(self.parser.known) | set(BUILTINS)) - {"run"})
        if not words:
            pool = commands
        elif len(words) > 1:
            pool = []
        elif SHORT.get(words[0], words[0]) in NUMBERED:
            pool = self.waiting_ids
        elif words[0] in NAMED:
            pool = self.names
        elif words[0] == "help":
            pool = commands
        else:
            pool = []
        return [word for word in pool if word.lower().startswith(text.lower())]

    def complete(self, text, state):
        try:
            found = self.candidates(readline.get_line_buffer()[:readline.get_begidx()], text)
            return found[state] + " " if state < len(found) else None
        except Exception:                       # readline swallows errors; never let one kill completion
            return None

    def start_readline(self):
        # Flip the flag only once readline has drawn the prompt, so a tick line that
        # arrives in between is printed plainly and not followed by a second prompt.
        readline.set_pre_input_hook(lambda: setattr(self, "prompting", True))
        readline.set_completer(self.complete)
        readline.set_completer_delims(" \t")
        readline.parse_and_bind("tab: complete")
        readline.set_history_length(1000)
        try:
            readline.read_history_file(HISTORY)
        except OSError:
            pass

    def stop_readline(self):
        readline.set_pre_input_hook(None)
        readline.set_completer(None)
        try:
            readline.write_history_file(HISTORY)
        except OSError:
            pass

    # -- the foreground loop -------------------------------------------------------------
    def run(self):
        sys.stdout = ThreadRouted(self, self.real)
        thread = threading.Thread(target=self.ticker, name="ticker", daemon=True)
        thread.start()
        self.emit(f"ticking every {self.every} seconds in the background. Type help, or quit to leave.")
        if readline:
            self.start_readline()
        try:
            while True:
                try:
                    self.refresh()
                except Exception as error:      # a locked or broken state.db must not end the session
                    self.emit(f"could not read state.db: {error}")
                try:
                    if not readline:
                        self.prompting = True
                    line = input(self.prompt)
                except EOFError:
                    self.prompting = False
                    self.emit()
                    break
                except KeyboardInterrupt:       # Ctrl+C at the prompt only cancels the line
                    self.prompting = False
                    self.emit("\n(type quit to leave)")
                    continue
                self.prompting = False
                if not self.handle(line):
                    break
        finally:
            self.leave(thread)

    def leave(self, thread):
        self.stopping = True                    # 4. leave only once a running tick is done
        self.wake.set()
        if self.work.locked():
            self.emit("finishing the tick in progress...")
        while thread.is_alive():
            try:
                thread.join(0.2)
            except KeyboardInterrupt:
                self.emit("\nstill finishing the tick; it would leave a bounty half-written. Waiting.")
        if readline:
            self.stop_readline()
        sys.stdout = self.real
        self.emit("stopped.")
