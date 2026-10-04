#!/usr/bin/env python3
"""Put a DM quest live (or take it down) in one command.

    python3 apply_quest.py test_quest.json
    python3 apply_quest.py test_quest.json --announce "A bounty has been posted in Northshire."
    python3 apply_quest.py test_quest.json --remove
    python3 apply_quest.py test_quest.json --dry-run

Steps, in order. Any failure stops the run before the next step.
  1. Check the spec (hot_quest.validate).
  2. Preflight against the live database: giver, targets and rewards exist.
     Nothing is written if a check fails.
  3. Write the quest to the world database.
  4. Tell the server to re-read quests through the remote console.
  5. Optionally announce it, because the quest marker does not appear for
     players already standing near the giver.

Settings are the ones in console.py plus:
    DM_COMPOSE_DIR   folder holding compose.yaml (default ~/cmangos-deploy)
    DM_DB_COMMAND    command that reads SQL on stdin; default runs the mariadb
                     client inside the database container
"""
import argparse
import json
import os
import shlex
import subprocess
import sys

import console
import hot_quest

DEFAULT_DB_COMMAND = (
    "docker compose exec -T database "
    "sh -c 'mariadb -t -u root -p\"$MARIADB_ROOT_PASSWORD\"'"
)


class StepFailed(Exception):
    pass


def run_sql(sql):
    """Feed SQL to the database and return its printed output."""
    stopped = console.paused()
    if stopped:
        raise StepFailed(f"the DM is paused ({stopped}); nothing was written")
    command = shlex.split(os.environ.get("DM_DB_COMMAND", DEFAULT_DB_COMMAND))
    folder = os.path.expanduser(os.environ.get("DM_COMPOSE_DIR", "~/cmangos-deploy"))
    try:
        done = subprocess.run(command, input=sql, text=True, capture_output=True,
                              cwd=folder if os.path.isdir(folder) else None, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StepFailed(f"could not run the database command: {error}") from None
    if done.returncode != 0:
        raise StepFailed(f"database rejected the SQL:\n{done.stderr.strip()}")
    return done.stdout


def apply_spec(spec, announce=None, remove=False, retire=False, say=print):
    """Validate a spec dict and put it live, retire it, or remove it.

    retire: stop offering it but keep the quest and everyone's history.
    remove: delete the quest and every character's record of it.

    Raises hot_quest.SpecError, StepFailed or console.ConsoleError. A
    ConsoleError means the database step already succeeded.
    """
    quest = hot_quest.validate(spec)
    commands = ["reload all_quest"]
    if announce and not remove and not retire:
        commands.append(f"announce {announce}")
    for command in commands:                          # refuse early, before any write
        console.check_allowed(console.normalise(command))

    label = f"quest {quest['id']} ({quest['title']})"
    if not remove and not retire:
        report = run_sql(hot_quest.render_preflight(quest))
        say(report.strip())
        if "PROBLEM" in report:
            raise StepFailed("preflight found a problem; nothing was written")

    if remove:
        run_sql(hot_quest.render_remove(quest))
    elif retire:
        run_sql(hot_quest.render_retire(quest))
    else:
        run_sql(hot_quest.render_apply(quest))
    say(f"database: {'removed' if remove else 'retired' if retire else 'wrote'} {label}")

    for command in commands:
        reply = console.run(command)
        say(f"console: {command} -> {reply or 'ok'}")
    return quest


def main():
    parser = argparse.ArgumentParser(description="Apply or remove a DM quest on the live server.")
    parser.add_argument("spec", help="path to the quest spec JSON")
    parser.add_argument("--remove", action="store_true", help="delete the quest and all record of it")
    parser.add_argument("--retire", action="store_true", help="stop offering the quest but keep its history")
    parser.add_argument("--announce", metavar="TEXT", help="server-wide chat line to send afterwards")
    parser.add_argument("--dry-run", action="store_true", help="print what would be done; change nothing")
    args = parser.parse_args()
    console.load_env()

    try:
        with open(args.spec, encoding="utf-8") as handle:
            spec = json.load(handle)
        spec = spec.get("spec", spec)       # accept the records write_quest.py saves
        quest = hot_quest.validate(spec)
    except (OSError, json.JSONDecodeError, hot_quest.SpecError) as error:
        sys.exit(f"apply_quest: spec rejected: {error}")

    if args.dry_run:
        print(hot_quest.render_remove(quest) if args.remove else
              hot_quest.render_retire(quest) if args.retire else hot_quest.render_apply(quest))
        print("-- console: reload all_quest")
        if args.announce and not args.remove:
            print(f"-- console: announce {args.announce}")
        return

    try:
        apply_spec(spec, announce=args.announce, remove=args.remove, retire=args.retire)
    except StepFailed as error:
        sys.exit(f"apply_quest: {error}")
    except console.ConsoleError as error:
        sys.exit(f"apply_quest: the database step succeeded but the console step did not.\n"
                 f"  {type(error).__name__}: {error}\n"
                 f"  The quest is in the database; run `.reload all_quest` in game to load it.")

    if args.retire:
        print("done. The quest is no longer offered; history is kept.")
    elif args.remove:
        print("done. Anyone holding the quest should relog.")
    else:
        print("done. The quest is on offer; players already near the giver will not see the marker yet.")


if __name__ == "__main__":
    main()
