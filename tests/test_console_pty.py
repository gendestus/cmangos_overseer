"""`dm.py run` at a terminal, typed at through a pseudo-terminal as a person would.

Runs tests/console_harness.py (the real DM, the game patched out) and types
while ticks run: a command during a tick, mistakes, a line half-typed when a
tick reports, and quit during a tick. About fifteen seconds.

    python3 -m unittest tests.test_console_pty
"""
import os
import select
import sys
import tempfile
import time
import unittest

try:
    import pty
    import readline     # noqa: F401 -- the redraw needs it; without it there is nothing to test
except ImportError:
    pty = None

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console_harness.py")


@unittest.skipUnless(pty, "needs a pseudo-terminal and readline")
class ConsoleAtATerminal(unittest.TestCase):
    def start(self, *args):
        env = dict(os.environ, DM_STATE=os.path.join(tempfile.mkdtemp(), "state.db"), TERM="xterm")
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, HARNESS, *args], env)
        self.pid, self.fd, self.out = pid, fd, b""
        return env

    def read_for(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                try:
                    self.out += os.read(self.fd, 4096)
                except OSError:
                    return

    def type(self, text, wait):
        os.write(self.fd, text.encode())
        self.read_for(wait)

    def finish(self):
        self.read_for(1.0)
        _, status = os.waitpid(self.pid, 0)
        os.close(self.fd)
        return os.waitstatus_to_exitcode(status), self.out.decode(errors="replace").replace("\r\n", "\n")

    def test_typing_while_ticks_run(self):
        self.start("--every", "4")
        self.read_for(0.6)                  # the first tick starts at once and takes 1.5 s
        self.type("status\n", 0.4)          # a console command while the tick runs
        self.type("pending\n", 2.0)         # a DM command while the tick runs: waits, then runs
        self.type("approve 9\n", 0.4)       # the command calls sys.exit
        self.type("approve x\n", 0.4)       # argparse refuses it
        self.type("frobnicate\n", 0.4)      # no such command
        self.type("every 2\n", 0.4)
        os.write(self.fd, b"appro")         # half-typed when the next tick reports
        self.read_for(4.5)
        self.type("ve 3\n", 0.8)            # finish the same line
        self.type("tick\n", 0.3)
        self.type("quit\n", 3.0)            # quit during the forced tick: it must finish first
        code, text = self.finish()

        self.assertEqual(code, 0, text)
        self.assertIn("a tick is running", text)
        self.assertIn("waiting for the tick to finish...", text)
        self.assertIn("no proposals waiting.", text)
        self.assertIn("dm: no pending proposal 9", text)
        self.assertIn("invalid int value: 'x'", text)
        self.assertIn("no such command: frobnicate", text)
        self.assertIn("dm: no pending proposal 3", text)     # the half-typed line arrived whole
        self.assertIn("finishing the tick in progress...", text)
        self.assertTrue(text.rstrip().endswith("stopped."), text)
        self.assertNotIn("overseer> overseer>", text)
        after_tick = text.split("approve x")[1]
        self.assertGreaterEqual(after_tick.count("tick: 0 character(s) online"), 2)   # ticks went on throughout
        self.assertRegex(text, r"tick: 0 character\(s\) online\noverseer> appro")   # the redraw kept the half line

    def test_ctrl_d_leaves_and_ctrl_c_only_cancels_the_line(self):
        self.start("--every", "60")
        self.read_for(2.5)
        self.type("half a line\x03", 0.5)
        self.type("status\n", 0.4)
        self.type("\x04", 1.0)
        code, text = self.finish()
        self.assertEqual(code, 0, text)
        self.assertIn("(type quit to leave)", text)
        self.assertIn("1 tick(s) so far", text)
        self.assertIn("stopped.", text)

    def test_a_second_run_is_refused(self):
        env = self.start("--every", "60")
        self.read_for(0.5)
        import subprocess
        second = subprocess.run([sys.executable, HARNESS, "--no-console"], env=env,
                                capture_output=True, text=True, timeout=10)
        self.type("quit\n", 2.5)
        self.finish()
        self.assertEqual(second.returncode, 1)
        self.assertIn("already running", second.stderr)

    def test_without_a_terminal_there_is_no_console(self):
        env = dict(os.environ, DM_STATE=os.path.join(tempfile.mkdtemp(), "state.db"), HARNESS_TICK_SECONDS="0",
                   PYTHONUNBUFFERED="1")
        import subprocess
        try:
            subprocess.run([sys.executable, HARNESS, "--every", "60"], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired as running:
            shown = running.stdout.decode() if isinstance(running.stdout, bytes) else (running.stdout or "")
        self.assertIn("Ctrl+C to stop", shown)
        self.assertNotIn("overseer>", shown)


if __name__ == "__main__":
    unittest.main()
