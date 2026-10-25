"""Run the Pins page script in node against stub browser globals."""

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

HARNESS = r"""
import { pathToFileURL } from 'node:url';
globalThis.document = { querySelector: () => null,
  querySelectorAll: () => [], elementFromPoint: () => null };
const mod = await import(pathToFileURL(process.argv[2]).href);
const results = {};
results.moved = [
  mod.movedOrder(['a', 'b', 'c'], 2, 0),
  mod.movedOrder(['a', 'b', 'c'], 0, 0),
];
const submits = [];
const form = {
  refs: { value: '' },
  requestSubmit: () => submits.push(form.refs.value),
};
const items = [];
const list = {
  querySelectorAll: (selector) =>
    selector === 'li' ? [...items] : items.map((item) => item.grip),
};
for (const ref of ['a', 'b', 'c']) {
  const item = { dataset: { ref }, attrs: {}, listeners: {} };
  item.grip = {
    closest: () => item,
    setPointerCapture() {},
    addEventListener: (name, fn) => { item.listeners[name] = fn; },
  };
  item.setAttribute = (name, value) => { item.attrs[name] = value; };
  item.removeAttribute = (name) => { delete item.attrs[name]; };
  item.closest = () => item;
  item.before = (moving) => {
    items.splice(items.indexOf(moving), 1);
    items.splice(items.indexOf(item), 0, moving);
  };
  item.after = (moving) => {
    items.splice(items.indexOf(moving), 1);
    items.splice(items.indexOf(item) + 1, 0, moving);
  };
  items.push(item);
}
const [a, , c] = items;
mod.wireDrag(list, form);
document.elementFromPoint = () => a;
c.listeners.pointerdown({ pointerId: 1 });
results.grabbed = c.attrs['aria-grabbed'];
c.listeners.pointermove({ clientX: 0, clientY: 0 });
c.listeners.pointerup({ pointerId: 1 });
results.drag = [form.refs.value, submits.length, c.attrs['aria-grabbed']];
submits.length = 0;
form.refs.value = '';
c.listeners.pointerdown({ pointerId: 1 });
document.elementFromPoint = () => c;
c.listeners.pointermove({ clientX: 0, clientY: 0 });
c.listeners.pointerup({ pointerId: 1 });
results.noMove = [form.refs.value, submits.length];

document.elementFromPoint = () => a;
c.listeners.pointerdown({ pointerId: 1 });
c.listeners.pointermove({ clientX: 0, clientY: 0 });
const mid = items.map((item) => item.dataset.ref).join();
c.listeners.pointercancel({ pointerId: 1 });
c.listeners.lostpointercapture({ pointerId: 1 });
results.cancel = [
  mid, items.map((item) => item.dataset.ref).join(),
  submits.length, c.attrs['aria-grabbed'] ?? null,
];

const editorSubmits = [];
const handlers = {};
const editor = {
  requestSubmit: () => editorSubmits.push(1),
  addEventListener: (name, fn) => { handlers[name] = fn; },
};
mod.wireEditor(editor);
handlers.change({ target: { name: 'kind' } });
handlers.change({ target: { name: 'nickname' } });
handlers.change({ target: { name: 'to' } });
results.editor = editorSubmits.length;

const deleteForm = {
  html: '', submitted: 0, listeners: {},
  addEventListener: (name, fn) => { deleteForm.listeners[name] = fn; },
  insertAdjacentHTML: (where, text) => { deleteForm.html += text; },
  submit: () => { deleteForm.submitted += 1; },
};
const question = { textContent: '' };
const dialogListeners = {};
const dialog = {
  opened: 0, returnValue: '',
  querySelector: () => question,
  showModal: () => {
    dialog.opened += 1;
    dialog.valueAtOpen = dialog.returnValue;
  },
  addEventListener: (name, fn) => { dialogListeners[name] = fn; },
};
document.querySelectorAll = () => [
  { form: deleteForm, dataset: { label: 'Work' } },
];
mod.wireDelete(dialog);
let prevented = 0;
deleteForm.listeners.submit({ preventDefault: () => { prevented += 1; } });
results.delete = [
  dialog.opened, deleteForm.submitted, prevented, question.textContent,
];
dialog.returnValue = 'delete';
dialogListeners.close();
results.confirmed = [deleteForm.submitted, deleteForm.html];
deleteForm.listeners.submit({ preventDefault() {} });
dialogListeners.close();
results.cancelled = [deleteForm.submitted, dialog.valueAtOpen];

results.renumbered = [
  mod.renumbered('w3_from', 1), mod.renumbered('nickname', 1),
];
const rows = [];
const node = (attrs, extra = {}) => ({
  attrs: { ...attrs },
  getAttribute(name) { return this.attrs[name] ?? null; },
  setAttribute(name, value) { this.attrs[name] = value; },
  ...extra,
});
const makeRow = (index) => {
  const day = node(
    { name: `w${index}_day` }, { type: 'checkbox', checked: true },
  );
  const from = node(
    { name: `w${index}_from`, id: `w${index}_from` },
    { type: 'time', value: '07:30' },
  );
  const label = node({ for: `w${index}_from` });
  const legend = { textContent: `Window ${index + 1}` };
  const row = { fields: [day, from, label], legend };
  const remove = node(
    { name: 'remove_window' },
    { name: 'remove_window', value: String(index) },
  );
  remove.closest = (selector) => (selector === 'button' ? remove : row);
  row.remove = () => rows.splice(rows.indexOf(row), 1);
  row.after = (other) => rows.splice(rows.indexOf(row) + 1, 0, other);
  row.cloneNode = () => makeRow(index);
  row.querySelector = (selector) =>
    selector === 'legend' ? legend : remove;
  row.querySelectorAll = (selector) =>
    selector === 'input' ? [day, from] : row.fields;
  row.button = remove;
  return row;
};
rows.push(makeRow(0), makeRow(1));
const count = { value: '2' };
const add = { name: 'add_window' };
add.closest = () => add;
const windowHandlers = {};
const outerLegend = {
  inserted: [],
  insertAdjacentHTML(where, html) { this.inserted.push([where, html]); },
};
const windowsSet = {
  querySelectorAll: () => [...rows],
  querySelector: (selector) =>
    selector === 'legend' ? outerLegend : count,
  addEventListener: (name, fn) => { windowHandlers[name] = fn; },
};
mod.wireWindows(windowsSet);
let windowPrevented = 0;
const press = (button) => windowHandlers.click({
  target: button, preventDefault: () => { windowPrevented += 1; },
});
press(rows[0].button);
const [left] = rows;
results.removed = [
  rows.length, count.value, left.legend.textContent, left.button.value,
  left.fields.map((field) => Object.values(field.attrs).join()),
];
press(add);
const [, added] = rows;
results.added = [
  rows.length, count.value, added.legend.textContent,
  added.fields[0].checked, added.fields[1].value,
  added.fields[1].attrs.name, windowPrevented,
];
windowHandlers.click({ target: { closest: () => null } });
const emptyBefore = outerLegend.inserted.length;
press(rows[1].button);
press(rows[0].button);
results.emptied = [
  rows.length, count.value, emptyBefore, outerLegend.inserted,
];
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_pins_script_in_node(tmp_path: Path) -> None:
    """Drag, submit-on-change, delete dialog and window rows behave."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'pins.mjs'
    script.write_text(
        (web_dir / 'pins.js').read_text(encoding='utf-8'),
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
        'moved': [['c', 'a', 'b'], ['a', 'b', 'c']],
        'grabbed': 'true',
        'drag': ['c,a,b', 1, None],
        'noMove': ['', 0],
        'cancel': ['a,c,b', 'c,a,b', 0, None],
        'cancelled': [1, ''],
        'editor': 2,
        'delete': [1, 0, 1, 'Delete “Work”?'],
        'confirmed': [
            1, '<input type="hidden" name="confirm" value="yes">',
        ],
        'renumbered': ['w1_from', 'nickname'],
        'removed': [
            1, '1', 'Window 1', '0', ['w0_day', 'w0_from,w0_from', 'w0_from'],
        ],
        'added': [2, '2', 'Window 2', False, '', 'w1_from', 2],
        'emptied': [
            0, '0', 0, [['afterend', '<p>Not shown automatically.</p>']],
        ],
    }
