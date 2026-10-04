#!/usr/bin/env python3
"""Two narrow database users, so the DM never speaks to the game as root.

    python3 db_users.py                 what exists now, and what each user may do
    python3 db_users.py --create        create or repair both users and their grants
    python3 db_users.py --create --write-env   ... and point .env at them
    python3 db_users.py --check         prove the reader cannot write and the writer cannot stray
    python3 db_users.py --drop          remove both users

The reader sees everything and changes nothing. The writer may touch only the
handful of tables a bounty lives in: it cannot read a player's mail, cannot
reach the account database, and cannot alter a creature or an item.

Both run inside the database container, so they are localhost users. Their
passwords are generated here and kept in `.env`; they are handed to the client
through MYSQL_PWD rather than on its command line, so no password sits in the
container's process list. On the host, `docker compose exec` does show it in
`ps` for the moment the query runs.

Root is still used for this script itself, through the same container as
before: the root password is read inside the container from
MARIADB_ROOT_PASSWORD and never passes through here.

Settings:
    DM_COMPOSE_DIR   folder holding compose.yaml (default ~/cmangos-deploy)
    DM_DB_SERVICE    compose service running the database (default "database")
"""
import argparse
import os
import re
import secrets
import shlex
import subprocess
import sys

import console
import hot_quest

READER = "dm_read"
WRITER = "dm_write"

# What the reader may see. It only ever runs SELECT, so whole databases are
# simpler than a table list that has to grow with every new query.
READ_GRANTS = (
    ("SELECT", f"{hot_quest.WORLD_DB}.*"),
    ("SELECT", f"{hot_quest.CHAR_DB}.*"),
)

# What the writer may change: a bounty's row, its two giver links, and the
# players' record of it. Table by table, because here the blast radius is real.
# Add a line when a phase needs a new table; `--create` is safe to re-run.
WRITE_GRANTS = (
    ("SELECT", f"{hot_quest.WORLD_DB}.creature_template"),   # preflight: the giver and targets exist
    ("SELECT", f"{hot_quest.WORLD_DB}.creature"),            # preflight: they are spawned
    ("SELECT", f"{hot_quest.WORLD_DB}.item_template"),       # preflight: reward items exist
    ("SELECT", f"{hot_quest.WORLD_DB}.spell_template"),      # preflight: taught spells exist
    ("SELECT, INSERT, DELETE", f"{hot_quest.WORLD_DB}.quest_template"),
    ("SELECT, INSERT, DELETE", f"{hot_quest.WORLD_DB}.creature_questrelation"),
    ("SELECT, INSERT, DELETE", f"{hot_quest.WORLD_DB}.creature_involvedrelation"),
    # Only when a quest is removed outright. SELECT comes with it because
    # MariaDB needs read access to the columns a DELETE's WHERE clause names.
    ("SELECT, DELETE", f"{hot_quest.CHAR_DB}.character_queststatus"),
)

ENV_KEYS = ("DM_DB_QUERY_COMMAND", "DM_DB_COMMAND")


class SetupFailed(Exception):
    pass


def compose_dir():
    folder = os.path.expanduser(os.environ.get("DM_COMPOSE_DIR", "~/cmangos-deploy"))
    return folder if os.path.isdir(folder) else None


def service():
    return os.environ.get("DM_DB_SERVICE", "database")


def client_command(user, password, flags="-N -B -r"):
    """The command `world_query` and `apply_quest` should run for this user."""
    return (f"docker compose exec -e MYSQL_PWD={shlex.quote(password)} -T {service()} "
            f"mariadb {flags} -u {user}")


def run(command, sql):
    try:
        done = subprocess.run(shlex.split(command), input=sql, text=True,
                              capture_output=True, cwd=compose_dir(), timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupFailed(f"could not reach the database container: {error}") from None
    return done


def as_root(sql):
    command = (f"docker compose exec -T {service()} "
               "sh -c 'mariadb -N -B -u root -p\"$MARIADB_ROOT_PASSWORD\"'")
    done = run(command, sql)
    if done.returncode != 0:
        raise SetupFailed(f"the database refused a root statement:\n{done.stderr.strip()}")
    return done.stdout


def as_user(user, password, sql):
    """Run SQL as one of the new users. Returns (ok, output or error)."""
    done = run(client_command(user, password), sql)
    return done.returncode == 0, (done.stdout if done.returncode == 0 else done.stderr).strip()


def existing_users():
    found = as_root("SELECT user FROM mysql.user WHERE host = 'localhost';")
    return {line.strip() for line in found.splitlines() if line.strip()}


def grants_of(user):
    try:
        found = as_root(f"SHOW GRANTS FOR {user}@localhost;")
    except SetupFailed:
        return []
    return [line.strip() for line in found.splitlines() if line.strip()]


def create(users):
    """Create or repair both users. Returns {user: password} for the ones made."""
    made = {}
    for user, grants in users:
        password = secrets.token_urlsafe(18)
        statements = [f"CREATE USER IF NOT EXISTS {user}@localhost IDENTIFIED BY {quote(password)};",
                      f"ALTER USER {user}@localhost IDENTIFIED BY {quote(password)};",
                      # Start from nothing, so a grant removed from the lists above
                      # is removed from the live user too.
                      f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {user}@localhost;"]
        for privilege, target in grants:
            statements.append(f"GRANT {privilege} ON {target} TO {user}@localhost;")
        statements.append("FLUSH PRIVILEGES;")
        as_root("\n".join(statements))
        made[user] = password
    return made


def drop():
    as_root(f"DROP USER IF EXISTS {READER}@localhost;\n"
            f"DROP USER IF EXISTS {WRITER}@localhost;\nFLUSH PRIVILEGES;")


def quote(text):
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


# Each probe: what it proves, the SQL, and whether it must succeed.
# The "must fail" writes name a row that cannot exist (id 0), so a user that
# wrongly has the privilege still changes nothing.
def probes():
    world, chars = hot_quest.WORLD_DB, hot_quest.CHAR_DB
    return {
        READER: [
            ("reads the world database", f"SELECT 1 FROM {world}.quest_template LIMIT 1;", True),
            ("reads the characters database", f"SELECT 1 FROM {chars}.characters LIMIT 1;", True),
            ("cannot write a quest", f"DELETE FROM {world}.quest_template WHERE entry = 0;", False),
            ("cannot write a creature", f"UPDATE {world}.creature_template SET Name = Name WHERE Entry = 0;", False),
            ("cannot reach the accounts", "SELECT 1 FROM realmd.account LIMIT 1;", False),
        ],
        WRITER: [
            ("preflights a giver", f"SELECT 1 FROM {world}.creature_template LIMIT 1;", True),
            ("writes a quest row", f"DELETE FROM {world}.quest_template WHERE entry = 0;", True),
            ("writes a giver link", f"DELETE FROM {world}.creature_questrelation WHERE quest = 0;", True),
            ("forgets a player's quest", f"DELETE FROM {chars}.character_queststatus WHERE quest = 0;", True),
            ("cannot change a creature", f"DELETE FROM {world}.creature WHERE guid = 0;", False),
            ("cannot change an item", f"DELETE FROM {world}.item_template WHERE entry = 0;", False),
            ("cannot read a player's mail", f"SELECT 1 FROM {chars}.mail LIMIT 1;", False),
            ("cannot read a character", f"SELECT 1 FROM {chars}.characters LIMIT 1;", False),
            ("cannot reach the accounts", "SELECT 1 FROM realmd.account LIMIT 1;", False),
        ],
    }


def check(passwords):
    """Run every probe. Returns the number that came out wrong."""
    wrong = 0
    for user, tests in probes().items():
        password = passwords.get(user)
        print(f"{user}:")
        if not password:
            print(f"  no password for {user} in .env; run --create first")
            wrong += len(tests)
            continue
        for label, sql, must_pass in tests:
            ok, output = as_user(user, password, sql)
            if ok == must_pass:
                print(f"  ok    {label}")
            else:
                wrong += 1
                detail = output.splitlines()[0] if output else ""
                print(f"  WRONG {label}" + (f": {detail}" if not ok else ": it was allowed"))
    return wrong


def env_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def env_lines(passwords):
    reader = client_command(READER, passwords[READER], flags="-N -B -r")
    writer = client_command(WRITER, passwords[WRITER], flags="-t")
    return [
        "# ---- Narrow database users (db_users.py) ----",
        "# Reads go through a user that cannot write; writes through one that can",
        "# only touch the tables a bounty lives in. Re-run `python3 db_users.py",
        "# --create --write-env` to rotate the passwords.",
        f"DM_DB_QUERY_COMMAND={reader}",
        f"DM_DB_COMMAND={writer}",
    ]


def write_env(passwords):
    """Replace the block this script owns in .env, leaving everything else alone."""
    path = env_path()
    try:
        with open(path, encoding="utf-8") as handle:
            existing = handle.read().splitlines()
    except FileNotFoundError:
        raise SetupFailed(f"no {path} to update; copy env.example to .env first") from None
    with open(path + ".bak", "w", encoding="utf-8") as handle:
        handle.write("\n".join(existing) + "\n")
    kept, skipping = [], False
    for line in existing:
        if line.startswith("# ---- Narrow database users"):
            skipping = True
            continue
        if skipping and (line.startswith("#") or not line.strip() or
                         any(line.startswith(key + "=") for key in ENV_KEYS)):
            continue
        skipping = False
        if any(line.startswith(key + "=") for key in ENV_KEYS):
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept + [""] + env_lines(passwords)) + "\n")
    return path


def passwords_from_env():
    """Dig the two passwords back out of the commands in .env."""
    console.load_env()
    found = {}
    for user, key in ((READER, "DM_DB_QUERY_COMMAND"), (WRITER, "DM_DB_COMMAND")):
        command = os.environ.get(key, "")
        match = re.search(r"MYSQL_PWD=(\S+)", command)
        if match and f"-u {user}" in command:
            found[user] = shlex.split(f"x={match.group(1)}")[0][2:]
    return found


def show():
    here = existing_users()
    for user in (READER, WRITER):
        if user not in here:
            print(f"{user}: does not exist")
            continue
        print(f"{user}:")
        for line in grants_of(user):
            print(f"  {line}")
    pointed = passwords_from_env()
    for user, key in ((READER, "DM_DB_QUERY_COMMAND"), (WRITER, "DM_DB_COMMAND")):
        print(f".env {key}: {'points at ' + user if user in pointed else 'not set (root is still used)'}")


def main():
    parser = argparse.ArgumentParser(description="Create the DM's narrow database users.")
    parser.add_argument("--create", action="store_true", help="create or repair both users and their grants")
    parser.add_argument("--write-env", action="store_true", help="with --create: point .env at the new users")
    parser.add_argument("--check", action="store_true", help="prove each user can do its job and no more")
    parser.add_argument("--drop", action="store_true", help="remove both users")
    args = parser.parse_args()
    console.load_env()

    try:
        if args.drop:
            drop()
            print(f"dropped {READER} and {WRITER}. Remove the two DM_DB_ lines from .env to go back to root.")
            return
        if args.create:
            made = create(((READER, READ_GRANTS), (WRITER, WRITE_GRANTS)))
            print(f"created {READER} and {WRITER} with fresh passwords.")
            if args.write_env:
                print(f"wrote {write_env(made)} (previous copy saved as .env.bak)")
            else:
                print("\nAdd these to .env:\n")
                print("\n".join(env_lines(made)))
            print()
            if check(passwords_from_env() if args.write_env else made):
                sys.exit("db_users: some grants are not what they should be; see above")
            print("\nevery probe behaved. The DM no longer needs root.")
            return
        if args.check:
            sys.exit(1 if check(passwords_from_env()) else 0)
        show()
    except SetupFailed as error:
        sys.exit(f"db_users: {error}")


if __name__ == "__main__":
    main()
