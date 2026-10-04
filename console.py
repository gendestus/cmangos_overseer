#!/usr/bin/env python3
"""Send GM commands to a CMaNGOS server through its SOAP remote console.

    python3 console.py "server info"
    python3 console.py "announce The Overseer is watching."

Commands run with console rights, which means every console-capable GM
command is available to whoever holds the SOAP account. The ALLOWED list
below is therefore the real permission boundary: anything not on it is
refused here, before it is sent.

Settings come from the environment, or from a `.env` file beside this script:

    DM_SOAP_URL    http://127.0.0.1:7878/
    DM_SOAP_USER   account name (needs account level 3)
    DM_SOAP_PASS   account password
"""
import base64
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

# Command prefix -> why the DM needs it. A command is allowed when it equals a
# prefix or starts with the prefix followed by a space.
ALLOWED = {
    "server info": "health check; changes nothing",
    "saveall": "write every online character to the database so positions are current",
    "reload all_quest": "pick up quests added or changed in the database",
    "reload npc_vendor": "pick up vendor stock changed in the database",
    "announce": "server-wide chat line",
    "notify": "server-wide on-screen message",
    "send mail": "letter to one character",
    "send items": "letter with items to one character",
    "send money": "letter with money to one character",
    "send message": "on-screen message to one character",
    "event start": "switch a prepared encounter on",
    "event stop": "switch a prepared encounter off",
}

ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<SOAP-ENV:Envelope'
    ' xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
    ' xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/"'
    ' xmlns:xsi="http://www.w3.org/1999/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/1999/XMLSchema"'
    ' xmlns:ns1="urn:MaNGOS">'
    '<SOAP-ENV:Body><ns1:executeCommand><command>{command}</command></ns1:executeCommand></SOAP-ENV:Body>'
    '</SOAP-ENV:Envelope>'
)


HERE = os.path.dirname(os.path.abspath(__file__))


class ConsoleError(Exception):
    """Base class: the command did not run successfully."""


class NotAllowed(ConsoleError):
    """The command is not on the ALLOWED list. Nothing was sent."""


class Unavailable(ConsoleError):
    """The SOAP port could not be reached."""


class AuthFailed(ConsoleError):
    """The server rejected the account."""


class CommandFailed(ConsoleError):
    """The server ran the command and reported failure."""


class Paused(ConsoleError):
    """The DM is paused, so nothing may reach the game."""


def pause_file():
    return os.environ.get("DM_PAUSE_FILE") or os.path.join(HERE, "paused")


def paused():
    """Why the DM is stopped, or None. The kill switch: nothing reaches the game.

    Set either way round: `dm.py pause "reason"` writes the flag file, or
    DM_PAUSE=1 in the environment. Reading the game is still allowed, so the
    chronicle and `pending` keep working while it is off.
    """
    load_env()
    if os.environ.get("DM_PAUSE", "").strip().lower() in ("1", "true", "yes", "on"):
        return "DM_PAUSE is set"
    try:
        with open(pause_file(), encoding="utf-8") as handle:
            return handle.read().strip() or "paused, with no reason given"
    except OSError:
        return None


def load_env(path=None):
    """Read KEY=VALUE lines from a .env file without overriding real env vars."""
    path = path or os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalise(command):
    """Strip the leading dot players type in game and collapse outer space."""
    command = command.strip()
    return command[1:].lstrip() if command.startswith(".") else command


def check_allowed(command):
    if any(ord(ch) < 32 for ch in command):
        raise NotAllowed("command contains a control character or line break")
    lowered = command.lower()
    for prefix in ALLOWED:
        if lowered == prefix or lowered.startswith(prefix + " "):
            return prefix
    raise NotAllowed(f"'{command.split(' ')[0]}...' is not on the allowed list")


def _find_text(body, local_name):
    """Text of the first element with this local name, whatever its namespace."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == local_name:
            return (element.text or "").strip()
    return None


def run(command, timeout=30):
    """Run one allowed command and return the server's text output."""
    load_env()
    command = normalise(command)
    check_allowed(command)
    stopped = paused()
    if stopped:
        raise Paused(f"the DM is paused ({stopped}); `{command}` was not sent")

    url = os.environ.get("DM_SOAP_URL", "http://127.0.0.1:7878/")
    user = os.environ.get("DM_SOAP_USER", "")
    password = os.environ.get("DM_SOAP_PASS", "")
    if not user or not password:
        raise AuthFailed("DM_SOAP_USER and DM_SOAP_PASS are not set (see env.example)")

    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=ENVELOPE.format(command=escape(command)).encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "Authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        body = error.read()
        if error.code == 401:
            raise AuthFailed("server rejected the account name or password") from None
        if error.code == 403:
            raise AuthFailed("account level is too low; SOAP needs level 3") from None
        message = _find_text(body, "faultstring")
        raise CommandFailed(message or f"HTTP {error.code} from the server") from None
    except (urllib.error.URLError, OSError) as error:
        raise Unavailable(f"cannot reach {url}: {getattr(error, 'reason', error)}") from None

    result = _find_text(body, "result")
    if result is None:
        fault = _find_text(body, "faultstring")
        raise CommandFailed(fault or "the server's reply had no result")
    return result.replace("\r\n", "\n")


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Allowed commands:")
        for prefix, why in ALLOWED.items():
            print(f"  {prefix:18} {why}")
        sys.exit(0 if len(sys.argv) == 2 else 2)
    try:
        print(run(sys.argv[1]))
    except ConsoleError as error:
        sys.exit(f"console: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
