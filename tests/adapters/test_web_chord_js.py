"""Run the page script's two-knob chord in node against stub globals."""

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const sent = [];
const listeners = {};
const timers = [];
globalThis.fetch = async (url, init) => {
  if (init) sent.push([init.body.get('knob'), init.body.get('action')]);
  return { ok: true, json: async () => ({}) };
};
globalThis.EventSource = class { addEventListener() {} };
globalThis.setInterval = () => 0;
globalThis.setTimeout = (fn, ms) => timers.push(fn) - 1;
globalThis.clearTimeout = (id) => { if (id !== null) timers[id] = null; };
const makeButton = (knob) => {
  const handlers = {};
  return {
    handlers,
    dataset: { knob, action: 'press' },
    addEventListener: (name, fn) => { handlers[name] = fn; },
  };
};
const top = makeButton('top');
const bottom = makeButton('bottom');
globalThis.document = {
  querySelector: () => ({ addEventListener() {}, textContent: '' }),
  querySelectorAll: () => [top, bottom],
  addEventListener: (name, fn) => { listeners[name] = fn; },
};
globalThis.window = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
};
await import(pathToFileURL(process.argv[2]).href);
const key = (name) => ({
  key: name, repeat: false, target: { closest: () => null },
});
const fire = () => timers.splice(0).forEach((fn) => fn && fn());
const down = (name) => listeners.keydown(key(name));
const up = (name) => listeners.keyup(key(name));
const results = {};
down('s'); down('k'); fire(); up('s'); up('k'); fire();
results.bothKeys = sent.splice(0);
down('s'); down('k'); up('k'); up('s'); fire();
results.bothEarly = sent.splice(0);
down('k'); fire(); down('s'); fire(); up('k'); up('s'); fire();
results.kHeldThenS = sent.splice(0);
const press = (button) => button.handlers.pointerdown({ button: 0 });
const release = (button) => {
  button.handlers.pointerup({ button: 0 });
  button.handlers.click({ detail: 1 });
};
press(top); press(bottom); fire(); release(top); release(bottom); fire();
results.bothButtons = sent.splice(0);
down('s'); up('s'); fire();
results.topTap = sent.splice(0);
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_page_chord_in_node(tmp_path: Path) -> None:
    """Both knobs held send one `both` hold and nothing of their own."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'live.mjs'
    script.write_text(
        (web_dir / 'live.js').read_text(encoding='utf-8'),
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
    both = [['both', 'hold']]
    assert json.loads(done.stdout) == {
        'bothKeys': both,
        'bothEarly': [],
        'kHeldThenS': both,
        'bothButtons': both,
        'topTap': [['top', 'press']],
    }
