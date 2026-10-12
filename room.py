#!/usr/bin/env python3
"""Writers' rooms: one-sided writers draft a plan, and a head writer weaves it.

Campaigns, chapters and arcs are each written by a room. The writers draft in
parallel, each from a deliberately one-sided point of view; the head writer
(the weaver) picks the strongest draft as a spine, grafts in the best of the
others, and writes the plan that is proposed. With no writers, the weaver
writes alone, which is the single call the DM made before rooms.

Each room is a JSON file in the writers folder, named for its stage:

    writers/campaign.json   writers/chapter.json   writers/arc.json

    {
      "writers": [
        {"name": "champion", "stance": "wants the character to succeed",
         "prompts": ["shared.md", "arc.md", "writer.md", "champion.md"], "max_tokens": 4096}
      ],
      "weaver": {"name": "weaver", "prompts": ["shared.md", "arc.md", "weaver.md"], "max_tokens": 6000}
    }

A member's system prompt is its prompt files, joined in order, then one line
naming the tool to call. Prompt paths are relative to the folder. "model" is
optional: without it a member uses the model the stage has always used.
Rooms are read on every call, so an edit counts from the next plan written.

What a plan may contain is not in these files: the tool schemas and the
validators in dm.py are the contract with state.db. A writer's tool is the
stage's own, with the fields only the weaver decides taken out.

A writer's draft is kept in state.db under a hash of everything that could
change it: prompt, model, token limit, tool and message. When the weaver's
answer is rejected, the next attempt with the same context calls only the
weaver. Drafts are kept for DRAFT_DAYS.

Settings:
    DM_WRITERS_ROOM   the writers folder (default: writers/ beside this script),
                      or "off": the weaver writes alone, from the default folder

    python3 room.py          check every room and list its members
"""
import concurrent.futures
import copy
import hashlib
import json
import os
import sys

import llm

STAGES = ("campaign", "chapter", "arc")
DEFAULT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "writers")
MEMBER_KEYS = {"name", "stance", "prompts", "max_tokens", "model"}
DRAFT_DAYS = 7
NOTE_LIMIT = 400


class RoomError(Exception):
    """A room file that cannot be used, named with the file and the member."""


def folder():
    chosen = os.environ.get("DM_WRITERS_ROOM", "").strip()
    return DEFAULT_FOLDER if not chosen or chosen.lower() == "off" else chosen


def writers_on():
    return os.environ.get("DM_WRITERS_ROOM", "").strip().lower() != "off"


def check_member(member, where, base, writer):
    if not isinstance(member, dict):
        raise RoomError(f"{where} is not an object")
    unknown = set(member) - MEMBER_KEYS
    if unknown:
        raise RoomError(f"{where} has unknown keys: {', '.join(sorted(unknown))} "
                        f"(allowed: {', '.join(sorted(MEMBER_KEYS))})")
    name = member.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RoomError(f"{where} needs a name")
    if writer and (not isinstance(member.get("stance"), str) or not member["stance"].strip()):
        raise RoomError(f"{where} ({name}) needs a stance: one line on what this writer wants")
    tokens = member.get("max_tokens")
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
        raise RoomError(f"{where} ({name}) needs max_tokens, a whole number above 0")
    if "model" in member and (not isinstance(member["model"], str) or not member["model"].strip()):
        raise RoomError(f"{where} ({name}) has an empty model; leave it out to use the stage's model")
    prompts = member.get("prompts")
    if not isinstance(prompts, list) or not prompts or not all(isinstance(p, str) for p in prompts):
        raise RoomError(f"{where} ({name}) needs prompts, a list of file names")
    texts = []
    for prompt in prompts:
        path = os.path.join(base, prompt)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError as error:
            raise RoomError(f"{where} ({name}): cannot read {path}: {error.strerror}") from None
        if not text:
            raise RoomError(f"{where} ({name}): {path} is empty")
        texts.append(text)
    return dict(member, name=name.strip(), text="\n\n".join(texts))


def load(stage):
    """The room for a stage: {"path", "writers": [...], "weaver": {...}}, each member with its prompt "text".

    Raises RoomError naming the file and the member at fault.
    """
    base = folder() if writers_on() else DEFAULT_FOLDER
    path = os.path.join(base, stage + ".json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as error:
        raise RoomError(f"cannot read {path}: {error.strerror}") from None
    except json.JSONDecodeError as error:
        raise RoomError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(data, dict) or set(data) - {"writers", "weaver"}:
        raise RoomError(f"{path} must be an object with \"writers\" and \"weaver\" and nothing else")
    if not isinstance(data.get("writers", []), list):
        raise RoomError(f"{path}: writers must be a list (it may be empty)")
    writers = [check_member(member, f"{path}: writer {number}", base, True)
               for number, member in enumerate(data.get("writers", []), 1)]
    names = [writer["name"] for writer in writers]
    if len(set(name.lower() for name in names)) != len(names):
        raise RoomError(f"{path}: writer names must be unique")
    if "weaver" not in data:
        raise RoomError(f"{path} needs a weaver")
    weaver = check_member(data["weaver"], f"{path}: weaver", base, False)
    return {"path": path, "writers": writers, "weaver": weaver}


def check_all():
    """[(stage, room or None, error or None)] for every stage."""
    found = []
    for stage in STAGES:
        try:
            found.append((stage, load(stage), None))
        except RoomError as error:
            found.append((stage, None, str(error)))
    return found


def system_for(member, tool):
    return member["text"] + f"\n\nRespond by calling the {tool['name']} tool exactly once. Do not reply with prose."


def draft_tool(stage, tool, omit):
    """The stage's tool for a writer: renamed, with the fields only the weaver decides taken out."""
    drafted = copy.deepcopy(tool)
    drafted["name"] = f"draft_{stage}"
    drafted["description"] = f"Submit your draft of the {stage} for the head writer."
    schema = drafted["input_schema"]
    for field in omit:
        schema["properties"].pop(field, None)
    schema["required"] = [field for field in schema.get("required", []) if field not in omit]
    return drafted


def weaver_tool(tool, names):
    """The stage's tool for a weaver with drafts: it also names its spine and says what it kept and cut."""
    woven = copy.deepcopy(tool)
    schema = woven["input_schema"]
    schema["properties"]["spine"] = {"type": "string", "enum": names,
                                     "description": "The writer whose draft this plan is built on."}
    schema["properties"]["notes"] = {
        "type": "array", "description": "One per writer: what you kept from the draft and what you cut.",
        "items": {"type": "object",
                  "properties": {"writer": {"type": "string", "enum": names},
                                 "kept": {"type": "string"}, "cut": {"type": "string"}},
                  "required": ["writer", "kept", "cut"]}}
    schema["required"] = list(schema.get("required", [])) + ["spine", "notes"]
    return woven


def draft_key(member, system, message, tool, model):
    provider = os.environ.get("DM_LLM_PROVIDER", "anthropic").lower()
    text = json.dumps([provider, llm.model_name(model), member["max_tokens"], system, message, tool],
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def fenced(value):
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def clean_notes(notes, names):
    by_lower = {name.lower(): name for name in names}
    found = []
    for note in notes if isinstance(notes, list) else []:
        if not isinstance(note, dict):
            continue
        writer = by_lower.get(str(note.get("writer", "")).strip().lower())
        if writer:
            found.append({"writer": writer,
                          "kept": " ".join(str(note.get("kept", "")).split())[:NOTE_LIMIT],
                          "cut": " ".join(str(note.get("cut", "")).split())[:NOTE_LIMIT]})
    return found


def write(db, stage, message, tool, omit=(), model=None):
    """Have the stage's room write a plan. Returns (answer, usage, record).

    answer is the weaver's tool arguments, for the stage's own validator. usage totals the tokens
    of every call made (a cached draft costs none) under the weaver's model. record is None when
    the weaver wrote alone; otherwise it holds the spine, the notes, each draft and each member's cost.
    Raises RoomError, or llm.LLMError when any call fails (drafts that did finish are kept).
    """
    room = load(stage)
    writers = room["writers"] if writers_on() else []
    weaver = room["weaver"]
    if not writers:
        answer, usage = llm.ask_for_tool_call(system_for(weaver, tool), message, tool,
                                              max_tokens=weaver["max_tokens"], model=weaver.get("model") or model)
        return answer, usage, None

    drafting = draft_tool(stage, tool, omit)
    asked = message + "\n\nWrite your draft for this character."
    jobs = []
    for writer in writers:
        system = system_for(writer, drafting)
        chosen = writer.get("model") or model
        jobs.append((writer, system, chosen, draft_key(writer, system, asked, drafting, chosen)))

    db.execute("DELETE FROM room_drafts WHERE created_at < strftime('%s', 'now') - ?", (DRAFT_DAYS * 86400,))
    drafts, members = {}, []
    for writer, _system, _chosen, key in jobs:
        row = db.execute("SELECT * FROM room_drafts WHERE key = ?", (key,)).fetchone()
        if row:
            drafts[writer["name"]] = json.loads(row["draft"])
            members.append({"name": writer["name"], "model": row["model"], "tokens_in": 0, "tokens_out": 0,
                            "cached": True})
    missing = [job for job in jobs if job[0]["name"] not in drafts]
    failure = None
    if missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(missing)) as pool:
            futures = [(job, pool.submit(llm.ask_for_tool_call, job[1], asked, drafting,
                                         max_tokens=job[0]["max_tokens"], model=job[2])) for job in missing]
            for (writer, _system, _chosen, key), future in futures:
                try:
                    draft, usage = future.result()
                except llm.LLMError as error:
                    failure = failure or llm.LLMError(f"writer {writer['name']}: {error}")
                    continue
                drafts[writer["name"]] = draft
                members.append({"name": writer["name"], "model": usage.get("model"),
                                "tokens_in": usage.get("input_tokens") or 0,
                                "tokens_out": usage.get("output_tokens") or 0, "cached": False})
                db.execute("INSERT OR REPLACE INTO room_drafts (key, stage, writer, draft, model, created_at) "
                           "VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))",
                           (key, stage, writer["name"], json.dumps(draft), usage.get("model")))
        db.commit()
    if failure:
        raise failure

    names = [writer["name"] for writer in writers]
    parts = [message, "Drafts from the writers, each from a one-sided point of view:"]
    for writer in writers:
        parts.append(f"Draft from `{writer['name']}`, who {writer['stance']}:\n\n" + fenced(drafts[writer["name"]]))
    parts.append("Write the plan that will be played.")
    woven = weaver_tool(tool, names)
    answer, usage = llm.ask_for_tool_call(system_for(weaver, woven), "\n\n".join(parts), woven,
                                          max_tokens=weaver["max_tokens"], model=weaver.get("model") or model)
    members.append({"name": weaver["name"], "model": usage.get("model"),
                    "tokens_in": usage.get("input_tokens") or 0, "tokens_out": usage.get("output_tokens") or 0,
                    "cached": False})
    spine = {name.lower(): name for name in names}.get(str(answer.get("spine", "")).strip().lower())
    record = {"spine": spine, "notes": clean_notes(answer.get("notes"), names),
              "drafts": {name: drafts[name] for name in names}, "members": members}
    total = {"model": usage.get("model"),
             "input_tokens": sum(member["tokens_in"] for member in members),
             "output_tokens": sum(member["tokens_out"] for member in members)}
    return answer, total, record


def record_lines(record, drafts=False):
    """A room's record as plain lines, for showing a proposal. With drafts, each writer's draft too."""
    if not record:
        return []
    cached = [m["name"] for m in record["members"] if m.get("cached")]
    lines = [f"Writers' room: built on {record['spine'] or 'no named draft'}"
             + (f"; drafts reused from an earlier attempt: {', '.join(cached)}" if cached else "")]
    for note in record["notes"]:
        lines.append(f"  {note['writer']}: kept {note['kept']} Cut: {note['cut']}")
    if drafts:
        for name, draft in record["drafts"].items():
            lines += [f"Draft from {name}:", json.dumps(draft, indent=2, ensure_ascii=False)]
    return lines


def main():
    state = "off: the weaver writes alone" if not writers_on() else folder()
    print(f"writers' rooms ({state})")
    bad = 0
    for stage, found, error in check_all():
        if error:
            bad += 1
            print(f"\n{stage}: ERROR {error}")
            continue
        print(f"\n{stage}: {found['path']}")
        for writer in found["writers"] if writers_on() else []:
            print(f"  writer  {writer['name']:<12} {writer['stance']}")
            print(f"          {' + '.join(writer['prompts'])}, {writer['max_tokens']} tokens"
                  + (f", model {writer['model']}" if writer.get("model") else ""))
        weaver = found["weaver"]
        print(f"  weaver  {weaver['name']:<12} {' + '.join(weaver['prompts'])}, {weaver['max_tokens']} tokens"
              + (f", model {weaver['model']}" if weaver.get("model") else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
