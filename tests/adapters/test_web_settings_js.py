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
let statusReply = null;
globalThis.fetch = (url, init) => {
  if (!init) {
    return new Promise((resolve) => { statusReply = resolve; });
  }
  sent.push([url, JSON.parse(init.body)]);
  const reply = replies.shift() ?? { ok: true };
  if (reply === 'throw') return Promise.reject(new Error('offline'));
  return Promise.resolve(reply);
};
const on = () => {
  const listeners = {};
  return {
    listeners,
    addEventListener: (name, fn) => { listeners[name] = fn; },
  };
};
const element = () => ({ ...on(), textContent: '', checked: true });
const nodes = {
  '#auto-rebuild': element(),
  '#timetable-save': element(),
  '#settings-status': element(),
};
globalThis.document = {
  querySelector: (selector) => nodes[selector],
};
await import(pathToFileURL(process.argv[2]).href);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const results = {};
const box = nodes['#auto-rebuild'];
const line = nodes['#settings-status'];
box.checked = false;
await box.listeners.change();
statusReply({ json: async () => ({ settings: { auto_rebuild: true } }) });
await tick();
results.loadKeepsToggle = box.checked;
replies.push({ ok: false, status: 403 });
box.checked = true;
await box.listeners.change();
results.refused = [line.textContent, box.checked];
replies.push('throw');
box.checked = true;
await box.listeners.change();
results.network = [line.textContent, box.checked];
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_settings_script_in_node(tmp_path: Path) -> None:
    """Toggles stick and failures say why."""
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
    assert json.loads(done.stdout) == {
        'loadKeepsToggle': False,
        'refused': ['Not saved', False],
        'network': ['Not saved: network error', False],
    }
