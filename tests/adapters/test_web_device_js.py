"""Run the Device page script in node against stub browser globals."""

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
const nodes = { '#device-status': { ...on(), textContent: '' } };
const forms = [];
const dialogs = [];
for (const action of ['reboot', 'poweroff']) {
  const form = { ...on(), dataset: { power: action } };
  const dialog = {
    ...on(), dataset: { power: action }, open: false, returnValue: 'x',
    showModal() { this.open = true; },
  };
  dialog.close = (value) => {
    dialog.open = false;
    dialog.returnValue = value;
    dialog.listeners.close({ currentTarget: dialog });
  };
  forms.push(form);
  dialogs.push(dialog);
  nodes[`#${action}-dialog`] = dialog;
}
globalThis.document = {
  querySelector: (selector) => nodes[selector],
  querySelectorAll: (selector) => (
    selector.startsWith('form') ? forms : dialogs
  ),
};
await import(pathToFileURL(process.argv[2]).href);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const results = {};
const line = nodes['#device-status'];
const off = forms[1];
const offDialog = dialogs[1];
let prevented = false;
off.listeners.submit({
  preventDefault: () => { prevented = true; }, currentTarget: off,
});
results.opened = [prevented, offDialog.open, offDialog.returnValue];
results.sentOnOpen = sent.splice(0);
offDialog.close('cancel');
await tick();
results.sentOnCancel = sent.splice(0);
off.listeners.submit({ preventDefault() {}, currentTarget: off });
offDialog.close('confirm');
await tick();
results.sentOnConfirm = sent.splice(0);
results.deviceStatus = line.textContent;
replies.push({ ok: false, status: 403 });
forms[0].listeners.submit({
  preventDefault() {}, currentTarget: forms[0],
});
dialogs[0].close('confirm');
await tick();
results.refused = line.textContent;
replies.push('throw');
forms[0].listeners.submit({
  preventDefault() {}, currentTarget: forms[0],
});
dialogs[0].close('confirm');
await tick();
results.network = line.textContent;
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_device_script_in_node(tmp_path: Path) -> None:
    """Power waits for the dialog, and failures say why."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'device.mjs'
    script.write_text(
        (web_dir / 'device.js').read_text(encoding='utf-8'),
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
        'opened': [True, True, ''],
        'sentOnOpen': [],
        'sentOnCancel': [],
        'sentOnConfirm': [['/power', {'action': 'poweroff'}]],
        'deviceStatus': 'Shutting down...',
        'refused': 'Not sent',
        'network': 'Not sent: network error',
    }
