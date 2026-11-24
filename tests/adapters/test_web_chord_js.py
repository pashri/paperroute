"""Run the page script's two-knob chord in node against stub globals."""

import shutil
from pathlib import Path

import pytest

from paperroute_device.adapters.web_frame import SHELL_WEB
from tests.adapters.node_harness import LIVE_PREAMBLE, run_in_node

HARNESS = LIVE_PREAMBLE + r"""
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
press(top); press(bottom); fire(); press(bottom); fire();
release(top); release(bottom); fire();
results.rePress = sent.splice(0);
press(top); press(bottom); fire(); release(top); release(bottom); fire();
press(top); press(bottom); fire(); release(top); release(bottom); fire();
results.twice = sent.splice(0);
down('s'); up('s'); fire();
results.topTap = sent.splice(0);
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_page_chord_in_node(tmp_path: Path) -> None:
    """Both knobs held send one `both` hold and nothing of their own."""
    both = [['both', 'hold']]
    assert run_in_node(
        tmp_path=tmp_path,
        source=SHELL_WEB / 'live.js',
        name='live.mjs',
        harness=HARNESS,
    ) == {
        'bothKeys': both,
        'bothEarly': [],
        'kHeldThenS': both,
        'bothButtons': both,
        'rePress': both,
        'twice': both + both,
        'topTap': [['top', 'press']],
    }
