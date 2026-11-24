"""Run the page script's status line in node against stub globals."""

import shutil
from pathlib import Path

import pytest

from paperroute_device.adapters.web_frame import SHELL_WEB
from tests.adapters.node_harness import run_in_node

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
    assert run_in_node(
        tmp_path=tmp_path,
        source=SHELL_WEB / 'live.js',
        name='live.mjs',
        harness=HARNESS,
    ) == {
        'line': 'X',
        'none': 'Status: -',
        'number': 'Status: -',
    }
