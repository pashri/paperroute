"""Run the page script's hold logic in node against stub browser globals."""

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
const stub = () => ({ addEventListener() {}, textContent: '' });
globalThis.fetch = async (url, init) => {
  if (init) sent.push(init.body.get('action'));
  return { ok: true, json: async () => ({}) };
};
globalThis.EventSource = class { addEventListener() {} };
globalThis.setInterval = () => 0;
globalThis.setTimeout = (fn, ms) => {
  fn.ms = ms;
  return timers.push(fn) - 1;
};
globalThis.clearTimeout = (id) => { if (id !== null) timers[id] = null; };
const pointer = {};
const button = {
  dataset: { knob: 'bottom', action: 'press' },
  addEventListener: (name, fn) => { pointer[name] = fn; },
};
const buttons = [button];
globalThis.document = {
  querySelector: stub,
  querySelectorAll: () => buttons,
  addEventListener: (name, fn) => { listeners[name] = fn; },
};
globalThis.window = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
};
await import(pathToFileURL(process.argv[2]).href);
const key = (name, extra = {}) => ({
  key: name, repeat: false, target: { closest: () => null }, ...extra,
});
const fire = () => timers.splice(0).forEach((fn) => fn && fn());
const results = {};
listeners.keydown(key('k'));
listeners.keyup(key('k'));
results.tap = sent.splice(0);
listeners.keydown(key('k'));
listeners.keydown(key('k', { repeat: true }));
fire();
listeners.keyup(key('k'));
results.hold = sent.splice(0);
listeners.keydown(key('s'));
listeners.keyup(key('s', { ctrlKey: true }));
fire();
results.modifier = sent.splice(0);
listeners.keydown(key('k'));
listeners.blur();
fire();
listeners.keyup(key('k'));
results.blur = sent.splice(0);
pointer.pointerdown({ button: 2 });
pointer.pointerup({ button: 2 });
fire();
results.rightClick = sent.splice(0);
const click = (detail) => pointer.click({ detail });
const down = () => pointer.pointerdown({ button: 0 });
const up = () => pointer.pointerup({ button: 0 });
down(); up(); click(1);
results.buttonTap = sent.splice(0);
fire();
results.buttonTapTimed = sent.splice(0);
down(); up(); click(1); down(); up(); click(2);
fire();
results.buttonDouble = sent.splice(0);
down();
const delays = timers.map((fn) => fn.ms);
fire();
up(); click(1);
fire();
results.buttonLong = sent.splice(0);
click(0);
results.keyboardClick = sent.splice(0);
results.keyboardTimers = timers.filter(Boolean).length;
results.delays = delays;
down(); fire(); pointer.pointerleave();
click(0);
results.leave = sent.splice(0);
down(); up(); click(1);
listeners.blur();
fire();
results.blurClick = sent.splice(0);
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_page_hold_timer_in_node(tmp_path: Path) -> None:
    """Keys and Press: click presses, double or long holds, none stranded."""
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
    assert done.stdout.strip() == (
        '{"tap":["press"],"hold":["hold"],'
        '"modifier":["press"],"blur":[],'
        '"rightClick":[],"buttonTap":[],"buttonTapTimed":["press"],'
        '"buttonDouble":["hold"],"buttonLong":["hold"],'
        '"keyboardClick":["press"],"keyboardTimers":0,"delays":[800],'
        '"leave":["hold","press"],"blurClick":[]}'
    )
