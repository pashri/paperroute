"""Run the setup page script in node against stub browser globals."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from paperroute_device.adapters.web_frame import SHELL_WEB

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const attributes = {};
const button = {
  hidden: true,
  textContent: 'Show password',
  listeners: {},
  addEventListener(name, fn) { this.listeners[name] = fn; },
  setAttribute(name, value) { attributes[name] = value; },
};
const password = { type: 'password' };
const nodes = { '#show-password': button, '#password': password };
globalThis.document = { querySelector: (selector) => nodes[selector] };
await import(pathToFileURL(process.argv[2]).href);
const seen = () => [
  button.hidden, password.type, attributes['aria-pressed'],
  button.textContent,
];
const results = { loaded: seen() };
button.listeners.click();
results.shown = seen();
button.listeners.click();
results.hiddenAgain = seen();
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_setup_script_in_node(tmp_path: Path) -> None:
    """The button appears and toggles the password between shown and not."""
    script = tmp_path / 'setup.mjs'
    script.write_text(
        (SHELL_WEB / 'setup.js').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    harness = tmp_path / 'harness.mjs'
    harness.write_text(HARNESS, encoding='utf-8')
    done = subprocess.run(
        ['node', str(harness), str(script)],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(done.stdout) == {
        'loaded': [False, 'password', 'false', 'Show password'],
        'shown': [False, 'text', 'true', 'Hide password'],
        'hiddenAgain': [False, 'password', 'false', 'Show password'],
    }
