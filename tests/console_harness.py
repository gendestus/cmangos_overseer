"""`dm.py run` with the game patched out, for test_console_pty.py to type at.

The world database, SOAP and the pause flag are replaced; everything else is
the real DM, with DM_STATE pointing at a scratch file. A tick sleeps a while
in "the world database" so a test can type while one is running.

    DM_STATE=/tmp/x/state.db python3 tests/console_harness.py --every 30
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import console          # noqa: E402
import dm               # noqa: E402
import dm_console       # noqa: E402
import world_query      # noqa: E402

TICK_SECONDS = float(os.environ.get("HARNESS_TICK_SECONDS", "1.5"))


def online_characters():
    time.sleep(TICK_SECONDS)            # a slow query, standing in for a model call
    return []


console.load_env = lambda: None         # the owner's .env must not reach a test
console.paused = lambda: None
console.run = lambda command: "ok"
world_query.online_characters = online_characters
world_query.parties = lambda: {}
world_query.dm_quest_progress = lambda: []
dm.observe_letters = lambda db, say: None
dm_console.HISTORY = os.path.join(os.path.dirname(os.environ["DM_STATE"]), ".dm_history")
dm.MIN_EVERY = dm_console.MIN_EVERY = 2  # a test cannot wait 30 seconds between ticks

if __name__ == "__main__":
    dm.main(["run"] + sys.argv[1:])
