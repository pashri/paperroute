"""A stand-in nmcli that records its arguments and gives set replies."""

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

type Reply = tuple[int, str]


def make_nmcli(
    *,
    tmp_path: Path,
    replies: Mapping[str, Reply | Sequence[Reply]],
) -> tuple[Path, Path]:
    """
    Write an executable stand-in for nmcli.

    Each run appends its arguments to the log as one JSON line, then
    prints the output and exits with the code of the first reply whose
    key starts the space-joined arguments; with no match it prints
    nothing and exits 0. A list of replies is given in turn, the last
    one repeating.

    Parameters
    ----------
    tmp_path : Path
        Folder to write the program and its log in.
    replies : Mapping[str, Reply | Sequence[Reply]]
        Exit code and output, or a list of them, by argument prefix.

    Returns
    -------
    tuple[Path, Path]
        The program, and its log.
    """
    log = tmp_path / 'nmcli.log'
    program = tmp_path / 'nmcli'
    turns = tmp_path / 'nmcli.turns'
    listed = {
        key: [value] if isinstance(value[0], int) else list(value)
        for key, value in replies.items()
    }
    program.write_text(
        f'#!{sys.executable}\n'
        'import json, os, sys\n'
        f'replies = json.loads({json.dumps(listed)!r})\n'
        f'turns_path = {str(turns)!r}\n'
        'turns = (\n'
        '    json.load(open(turns_path)) if os.path.exists(turns_path)\n'
        '    else {}\n'
        ')\n'
        f'with open({str(log)!r}, "a", encoding="utf-8") as out:\n'
        '    out.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        'joined = " ".join(sys.argv[1:])\n'
        'key = next((k for k in replies if joined.startswith(k)), None)\n'
        'code, text = 0, ""\n'
        'if key is not None:\n'
        '    turn = turns.get(key, 0)\n'
        '    code, text = replies[key][min(turn, len(replies[key]) - 1)]\n'
        '    turns[key] = turn + 1\n'
        '    json.dump(turns, open(turns_path, "w"))\n'
        'sys.stdout.write(text)\n'
        'raise SystemExit(code)\n',
        encoding='utf-8',
    )
    program.chmod(0o755)
    return program, log


def calls(*, log: Path) -> list[list[str]]:
    """
    Read the argument lists the stand-in was run with.

    Parameters
    ----------
    log : Path
        The stand-in's log.

    Returns
    -------
    list[list[str]]
        Arguments of each run, in order; empty when it never ran.
    """
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding='utf-8').splitlines()
    ]
