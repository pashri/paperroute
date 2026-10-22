"""A stand-in nmcli that records its arguments and gives set replies."""

import json
import sys
from collections.abc import Mapping
from pathlib import Path


def make_nmcli(
    *, tmp_path: Path, replies: Mapping[str, tuple[int, str]],
) -> tuple[Path, Path]:
    """
    Write an executable stand-in for nmcli.

    Each run appends its arguments to the log as one JSON line, then
    prints the output and exits with the code of the first reply whose
    key starts the space-joined arguments; with no match it prints
    nothing and exits 0.

    Parameters
    ----------
    tmp_path : Path
        Folder to write the program and its log in.
    replies : Mapping[str, tuple[int, str]]
        Exit code and output by argument prefix.

    Returns
    -------
    tuple[Path, Path]
        The program, and its log.
    """
    log = tmp_path / 'nmcli.log'
    program = tmp_path / 'nmcli'
    program.write_text(
        f'#!{sys.executable}\n'
        'import json, sys\n'
        f'replies = json.loads({json.dumps(dict(replies))!r})\n'
        f'with open({str(log)!r}, "a", encoding="utf-8") as out:\n'
        '    out.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        'joined = " ".join(sys.argv[1:])\n'
        'code, text = next(\n'
        '    (v for k, v in replies.items() if joined.startswith(k)),\n'
        '    (0, ""),\n'
        ')\n'
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
