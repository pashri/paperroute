"""Run the Device page script in node against stub browser globals."""

import json
import shutil
import subprocess
from typing import Any

import pytest

from paperroute_device.adapters.web_frame import SHELL_WEB

pytestmark = pytest.mark.skipif(
    shutil.which('node') is None, reason='node not installed',
)

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const withSwitch = process.argv[3] === 'switch';
const sent = [];
const replies = [];
let release = () => {};
globalThis.fetch = (url, init) => {
  sent.push([url, JSON.parse(init.body)]);
  const reply = replies.shift() ?? { ok: true };
  if (reply === 'throw') return Promise.reject(new Error('offline'));
  if (reply === 'hold') {
    return new Promise((resolve) => {
      release = () => resolve({ ok: true });
    });
  }
  return Promise.resolve(reply);
};
const on = () => {
  const listeners = {};
  return {
    listeners,
    addEventListener: (name, fn) => { listeners[name] = fn; },
  };
};
const modal = (dataset) => {
  const dialog = {
    ...on(), dataset, open: false, returnValue: 'x',
    showModal() { this.open = true; },
  };
  dialog.close = (value) => {
    dialog.open = false;
    dialog.returnValue = value;
    dialog.listeners.close({ currentTarget: dialog });
  };
  return dialog;
};
const nodes = {
  '#device-status': { ...on(), textContent: '' },
  '#switch-status': { ...on(), textContent: '' },
};
const forms = [];
const dialogs = [];
for (const action of ['reboot', 'poweroff']) {
  const dialog = modal({ power: action });
  forms.push({ ...on(), dataset: { power: action } });
  dialogs.push(dialog);
  nodes[`#${action}-dialog`] = dialog;
}
const radios = [
  { value: 'first', disabled: true, checked: false, dataset: {} },
  { value: 'demo', checked: true, dataset: { name: 'Demo' } },
  { value: 'third', checked: false, dataset: { name: 'Third' } },
];
const button = { textContent: 'Switch' };
const switchForm = {
  ...on(),
  querySelector: (selector) => ({
    'input[name="app"]:checked': radios.find((radio) => radio.checked),
    'button[type="submit"]': button,
    ...Object.fromEntries(radios.map((radio) => [
      `input[name="app"][value="${radio.value}"]`, radio,
    ])),
  })[selector] ?? null,
};
const switchDialog = modal({ switch: 'demo' });
nodes['#switch-demo-dialog'] = switchDialog;
if (withSwitch) nodes['form[data-switch]'] = switchForm;
const lists = {
  'form[data-power]': forms,
  'dialog[data-power]': dialogs,
  'dialog[data-switch]': [switchDialog],
};
globalThis.document = {
  querySelector: (selector) => nodes[selector] ?? null,
  querySelectorAll: (selector) => lists[selector] ?? [],
};
await import(pathToFileURL(process.argv[2]).href);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const power = {};
const line = nodes['#device-status'];
const off = forms[1];
const offDialog = dialogs[1];
let prevented = false;
off.listeners.submit({
  preventDefault: () => { prevented = true; }, currentTarget: off,
});
power.opened = [prevented, offDialog.open, offDialog.returnValue];
power.sentOnOpen = sent.splice(0);
offDialog.close('cancel');
await tick();
power.sentOnCancel = sent.splice(0);
off.listeners.submit({ preventDefault() {}, currentTarget: off });
offDialog.close('confirm');
await tick();
power.sentOnConfirm = sent.splice(0);
power.deviceStatus = line.textContent;
replies.push({ ok: false, status: 403 });
forms[0].listeners.submit({
  preventDefault() {}, currentTarget: forms[0],
});
dialogs[0].close('confirm');
await tick();
power.refused = line.textContent;
replies.push('throw');
forms[0].listeners.submit({
  preventDefault() {}, currentTarget: forms[0],
});
dialogs[0].close('confirm');
await tick();
power.network = line.textContent;
sent.splice(0);
const status = nodes['#switch-status'];
const app = {
  listeners: Object.keys(switchForm.listeners).length
    + Object.keys(switchDialog.listeners).length,
  button: button.textContent,
};
const ask = () => {
  let stopped = false;
  switchForm.listeners.submit({
    preventDefault: () => { stopped = true; }, currentTarget: switchForm,
  });
  return [stopped, switchDialog.open, switchDialog.returnValue];
};
if (withSwitch) {
  app.opened = ask();
  switchDialog.close('cancel');
  await tick();
  app.cancelled = [sent.splice(0), status.textContent];
  ask();
  switchDialog.close('confirm');
  await tick();
  app.confirmed = [sent.splice(0), status.textContent];
  replies.push({ ok: false, status: 409 });
  ask();
  switchDialog.close('confirm');
  await tick();
  app.refused = status.textContent;
  replies.push('hold');
  ask();
  switchDialog.close('confirm');
  radios[1].checked = false;
  radios[2].checked = true;
  release();
  await tick();
  app.rechosen = status.textContent;
}
app.status = status.textContent;
console.log(JSON.stringify({ power, app }));
"""


@pytest.fixture(name='pages', scope='module')
def _pages(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The script's results on a page with the switch form and without."""
    folder = tmp_path_factory.mktemp('device-js')
    script = folder / 'device.mjs'
    script.write_text(
        (SHELL_WEB / 'device.js').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    harness = folder / 'harness.mjs'
    harness.write_text(HARNESS, encoding='utf-8')
    return {
        page: json.loads(
            subprocess.run(
                ['node', str(harness), str(script), page],
                capture_output=True,
                check=True,
                text=True,
            ).stdout,
        )
        for page in ('switch', 'bare')
    }


def test_device_script_in_node(pages: dict[str, Any]) -> None:
    """Power waits for the dialog, and failures say why."""
    for page in ('switch', 'bare'):
        assert pages[page]['power'] == {
            'opened': [True, True, ''],
            'sentOnOpen': [],
            'sentOnCancel': [],
            'sentOnConfirm': [['/power', {'action': 'poweroff'}]],
            'deviceStatus': 'Shutting down...',
            'refused': 'Not sent',
            'network': 'Not sent: network error',
        }


def test_switch_asks_in_a_dialog_then_posts(pages: dict[str, Any]) -> None:
    """The switch opens the chosen app's dialog, then posts once confirmed."""
    app = pages['switch']['app']
    assert app['opened'] == [True, True, '']
    assert app['confirmed'] == [
        [['/switch', {'app': 'demo'}]], 'Switching to Demo...',
    ]


def test_switch_cancel_sends_nothing(pages: dict[str, Any]) -> None:
    """Cancelling the dialog sends nothing and says nothing."""
    assert pages['switch']['app']['cancelled'] == [[], '']


def test_switch_refusal_says_not_switched(pages: dict[str, Any]) -> None:
    """A refused switch says it did not happen."""
    assert pages['switch']['app']['refused'] == 'Not switched'


def test_switch_names_the_app_it_posted(pages: dict[str, Any]) -> None:
    """A radio chosen while the post is out does not change the name."""
    assert pages['switch']['app']['rechosen'] == 'Switching to Demo...'


def test_button_names_the_chosen_app(pages: dict[str, Any]) -> None:
    """Once loaded, the button names the app it switches to."""
    assert pages['switch']['app']['button'] == 'Switch to Demo'


def test_page_without_the_form_wires_no_switch(pages: dict[str, Any]) -> None:
    """With no switch form nothing listens and no status is written."""
    app = pages['bare']['app']
    assert app['listeners'] == 0
    assert app['status'] == ''
    assert app['button'] == 'Switch'
