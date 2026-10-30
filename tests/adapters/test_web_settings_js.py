"""Run the Settings page script in node against stub browser globals."""

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const sent = [];
const replies = [];
globalThis.fetch = (url, init) => {
  sent.push([url, JSON.parse(init.body)]);
  const reply = replies.shift() ?? { status: 204 };
  if (reply === 'throw') return Promise.reject(new Error('offline'));
  return reply.promise ?? Promise.resolve(reply);
};
const held = (reply) => {
  let release;
  const promise = new Promise((resolve) => {
    release = () => resolve(reply);
  });
  return { promise, release: () => release() };
};
const BAD_TIME = 'Quiet hours not saved: use HH:MM times.';
const badTime = () => ({
  status: 422,
  json: async () => ({ error: BAD_TIME }),
});
globalThis.FormData = class {
  constructor(form) {
    this.pairs = form.entries.map((pair) => [...pair]);
  }

  [Symbol.iterator]() {
    return this.pairs[Symbol.iterator]();
  }
};
const fakeForm = (entries, extra = {}) => {
  const listeners = {};
  const nodes = {
    '.saved': { textContent: 'old' },
    'button[type="submit"]': { hidden: false },
    ...extra,
  };
  return {
    entries,
    listeners,
    nodes,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    querySelector: (selector) => nodes[selector],
  };
};
const errorLine = { gone: false, remove() { this.gone = true; } };
const quiet = fakeForm(
  [['quiet_mode', 'my_hours'], ['quiet_start', '22:00']],
  { '.error': errorLine },
);
const display = fakeForm([
  ['rotate', 'off'], ['rotate', 'on'], ['peak_rate', '60s'],
]);
globalThis.document = {
  querySelectorAll: (selector) => (
    selector === '.setting form' ? [quiet, display] : []
  ),
};
await import(pathToFileURL(process.argv[2]).href);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const note = (form) => form.nodes['.saved'].textContent;
const change = async (form) => {
  form.listeners.change();
  await tick();
};
const results = {};
let prevented = false;
quiet.listeners.submit({ preventDefault: () => { prevented = true; } });
await tick();
results.submit = [prevented, sent.splice(0).length];
results.hidesSave = [quiet, display].map(
  (form) => form.nodes['button[type="submit"]'].hidden,
);
await change(display);
results.postsCard = sent.splice(0);
results.saved = note(display);
replies.push(badTime());
await change(quiet);
results.badTime = note(quiet);
replies.push({ status: 400 });
await change(quiet);
results.refused = note(quiet);
replies.push('throw');
await change(quiet);
results.network = note(quiet);
sent.length = 0;
const first = held(badTime());
const second = held({ status: 204 });
replies.push(first, second);
await change(quiet);
results.cleared = note(quiet);
quiet.entries[1][1] = '23:00';
await change(quiet);
const whileHeld = sent.length;
first.release();
await tick();
const afterFirst = note(quiet);
const afterRelease = sent.length;
second.release();
await tick();
results.oneAtATime = [whileHeld, afterRelease, sent[1][1]];
results.latestWins = [afterFirst, note(quiet)];
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_settings_script_in_node(tmp_path: Path) -> None:
    """Each card saves on change, one post at a time, and says how."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'settings.mjs'
    script.write_text(
        (web_dir / 'settings.js').read_text(encoding='utf-8'),
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
    results = json.loads(done.stdout)
    assert results['hidesSave'] == [True, True]
    assert results['postsCard'] == [
        ['/settings', {'rotate': 'on', 'peak_rate': '60s'}],
    ]
    assert results['saved'] == 'Saved'
    assert results['badTime'] == 'Quiet hours not saved: use HH:MM times.'
    assert results['submit'] == [True, 1]
    assert results['refused'] == 'Not saved'
    assert results['network'] == 'Not saved: network error'
    assert results['cleared'] == ''
    assert results['oneAtATime'] == [
        1, 2, {'quiet_mode': 'my_hours', 'quiet_start': '23:00'},
    ]
    assert results['latestWins'] == ['', 'Saved']
