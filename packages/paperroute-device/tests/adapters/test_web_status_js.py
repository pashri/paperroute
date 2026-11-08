"""Run the page script's status line in node against stub globals."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from paperroute_device.adapters.web_frame import SHELL_WEB

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const elements = {};
let reply = {};
globalThis.fetch = async () => ({ ok: true, json: async () => reply });
globalThis.EventSource = class { addEventListener() {} };
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.document = {
  querySelector: (selector) => {
    elements[selector] ??= { addEventListener() {}, textContent: '' };
    return elements[selector];
  },
  querySelectorAll: () => [],
  addEventListener: () => {},
};
globalThis.window = { addEventListener: () => {} };
const shown = async (status, run) => {
  reply = status;
  await import(`${pathToFileURL(process.argv[2]).href}?${run}`);
  await new Promise((resolve) => setImmediate(resolve));
  return elements['#status'].textContent;
};
console.log(JSON.stringify({
  line: await shown({ line: 'X' }, 1),
  none: await shown({}, 2),
  number: await shown({ line: 3 }, 3),
}));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_status_line_shows_app_text(tmp_path: Path) -> None:
    """The status line shows the app's text, or a dash without one."""
    script = tmp_path / 'live.mjs'
    script.write_text(
        (SHELL_WEB / 'live.js').read_text(encoding='utf-8'),
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
        'line': 'X',
        'none': 'Status: -',
        'number': 'Status: -',
    }
