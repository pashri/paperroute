"""Run a page script in node under a test harness."""

import json
import subprocess
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

FORMS_PREAMBLE: Final[str] = r"""
import { pathToFileURL } from 'node:url';
globalThis.FormData = class {
  constructor(form) { this.entries = form.fields; }
  [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
};
"""
LIVE_PREAMBLE: Final[str] = r"""
import { pathToFileURL } from 'node:url';
const sent = [];
const listeners = {};
const timers = [];
"""


def run_in_node(
    *, tmp_path: Path, source: Traversable, name: str, harness: str,
) -> object:
    """
    Copy a script to a module file and run it under a harness in node.

    Parameters
    ----------
    tmp_path : Path
        Folder to write the script and harness in.
    source : Traversable
        The page script to run.
    name : str
        File name for the module copy of the script.
    harness : str
        The harness code; it takes the script's path as its argument and
        prints JSON.

    Returns
    -------
    object
        The JSON the harness printed.
    """
    script = tmp_path / name
    script.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    runner = tmp_path / 'harness.mjs'
    runner.write_text(harness, encoding='utf-8')
    done = subprocess.run(
        ['node', str(runner), str(script)],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(done.stdout)
