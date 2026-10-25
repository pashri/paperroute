/**
 * Move one ref to a new place in the order.
 * @param {string[]} refs The refs in their current order.
 * @param {number} from The index of the ref to move.
 * @param {number} to The index it should end up at.
 * @returns {string[]} A new array in the new order.
 */
export function movedOrder(refs, from, to) {
  const next = [...refs];
  const [ref] = next.splice(from, 1);
  next.splice(to, 0, ref);
  return next;
}

/**
 * Read the refs of the list's items in screen order.
 * @param {Element} list The ordered list of pins.
 * @returns {string[]} The refs.
 */
function refsOf(list) {
  return [...list.querySelectorAll('li')].map((li) => li.dataset.ref);
}

/**
 * Find an item's index among the list's items.
 * @param {Element} list The ordered list of pins.
 * @param {Element} item One of the items.
 * @returns {number} Its index.
 */
function indexIn(list, item) {
  return [...list.querySelectorAll('li')].indexOf(item);
}

/**
 * Put the dragged item at an index among the list's items.
 * @param {Element} list The ordered list of pins.
 * @param {Element} item The item being dragged.
 * @param {number} index Where it should sit once the others are in place.
 * @returns {void}
 */
function place(list, item, index) {
  const others = [...list.querySelectorAll('li')].filter((li) => li !== item);
  if (index >= others.length) others[others.length - 1].after(item);
  else others[index].before(item);
}

/**
 * Submit the new order when a drop changed it.
 * @param {HTMLFormElement} form The order form.
 * @param {string[]} before The refs when the drag began.
 * @param {number} from The dragged item's index when the drag began.
 * @param {number} to The dragged item's index now.
 * @returns {void}
 */
function drop(form, before, from, to) {
  const after = movedOrder(before, from, to);
  if (before.join() === after.join()) return;
  form.refs.value = after.join(',');
  form.requestSubmit();
}

/**
 * Let one grip drag its item up and down the list.
 * @param {Element} list The ordered list of pins.
 * @param {HTMLFormElement} form The order form.
 * @param {Element} grip The grip to wire.
 * @returns {void}
 */
function wireGrip(list, form, grip) {
  const item = grip.closest('li');
  let drag = null;
  const end = () => {
    item.removeAttribute('aria-grabbed');
    drag = null;
  };
  grip.addEventListener('pointerdown', (event) => {
    grip.setPointerCapture(event.pointerId);
    item.setAttribute('aria-grabbed', 'true');
    const before = refsOf(list);
    drag = { before, from: before.indexOf(item.dataset.ref) };
  });
  grip.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const under = document.elementFromPoint(event.clientX, event.clientY);
    const over = under && under.closest('li');
    if (over && over !== item) place(list, item, indexIn(list, over));
  });
  grip.addEventListener('pointerup', () => {
    if (!drag) return;
    const { before, from } = drag;
    end();
    drop(form, before, from, indexIn(list, item));
  });
  const cancel = () => {
    if (!drag) return;
    place(list, item, drag.from);
    end();
  };
  grip.addEventListener('pointercancel', cancel);
  grip.addEventListener('lostpointercapture', cancel);
}

/**
 * Drag pins by their grips to reorder them, then submit the order.
 * @param {Element} list The ordered list of pins.
 * @param {HTMLFormElement} form The order form with a `refs` field.
 * @returns {void}
 */
export function wireDrag(list, form) {
  for (const grip of list.querySelectorAll('.grip')) {
    wireGrip(list, form, grip);
  }
}

const CHOICES = new Set(['kind', 'route', 'to']);

/**
 * Refresh the editor's choices as soon as a mode or select changes.
 * @param {HTMLFormElement} form The pin editor form.
 * @returns {void}
 */
export function wireEditor(form) {
  form.addEventListener('change', (event) => {
    if (CHOICES.has(event.target.name)) form.requestSubmit();
  });
}

/**
 * Ask in a dialog before a pin is deleted.
 * @param {HTMLDialogElement} dialog The confirmation dialog.
 * @param {HTMLFormElement} form The delete form that was submitted.
 * @param {string} label The pin's name.
 * @returns {void}
 */
function askToDelete(dialog, form, label) {
  const question = dialog.querySelector('#delete-question');
  question.textContent = `Delete “${label}”?`;
  dialog.returnValue = '';
  dialog.addEventListener('close', () => {
    if (dialog.returnValue !== 'delete') return;
    form.insertAdjacentHTML(
      'beforeend', '<input type="hidden" name="confirm" value="yes">',
    );
    form.submit();
  }, { once: true });
  dialog.showModal();
}

/**
 * Open the dialog when a Delete form is submitted.
 * @param {HTMLDialogElement} dialog The confirmation dialog.
 * @returns {void}
 */
export function wireDelete(dialog) {
  for (const button of document.querySelectorAll('.pins [data-label]')) {
    button.form.addEventListener('submit', (event) => {
      event.preventDefault();
      askToDelete(dialog, button.form, button.dataset.label);
    });
  }
}

const NUMBERED = /^w\d+_/;
const NUMBERED_ATTRS = ['name', 'id', 'for'];
const MAX_ROWS = 20;

/**
 * Give a window row's field name or id a new row number.
 * @param {string} name A name such as `w3_from`.
 * @param {number} index The new row index.
 * @returns {string} `w{index}_{field}`, or the name itself when it is
 *   not a window row's.
 */
export function renumbered(name, index) {
  return name.replace(NUMBERED, `w${index}_`);
}

/**
 * Renumber one field's name, id and label target.
 * @param {Element} field A field or label in a row.
 * @param {number} index The row's index.
 * @returns {void}
 */
function renumberField(field, index) {
  for (const attr of NUMBERED_ATTRS) {
    const value = field.getAttribute(attr);
    if (value) field.setAttribute(attr, renumbered(value, index));
  }
}

/**
 * Renumber a row's legend, Remove value and fields.
 * @param {Element} row A window row.
 * @param {number} index Its index.
 * @returns {void}
 */
function renumberRow(row, index) {
  row.querySelector('legend').textContent = `Window ${index + 1}`;
  row.querySelector('[name="remove_window"]').value = String(index);
  for (const field of row.querySelectorAll('[name], [id], [for]')) {
    renumberField(field, index);
  }
}

/**
 * Renumber every row and update the row count.
 * @param {Element} fieldset The Show automatically fieldset.
 * @returns {void}
 */
function renumberAll(fieldset) {
  const rows = [...fieldset.querySelectorAll('fieldset.window')];
  rows.forEach((row, index) => renumberRow(row, index));
  fieldset.querySelector('[name="wn"]').value = String(rows.length);
}

/**
 * Copy a row with its days unticked and its times cleared.
 * @param {Element} row A window row.
 * @returns {Element} The blank copy.
 */
function blankRow(row) {
  const copy = row.cloneNode(true);
  for (const input of copy.querySelectorAll('input')) {
    if (input.type === 'time') input.value = '';
    else input.checked = false;
  }
  return copy;
}

/**
 * Add a blank row after the last one, when there is a row to copy.
 * @param {Element} fieldset The Show automatically fieldset.
 * @returns {boolean} Whether a row was added.
 */
function addRow(fieldset) {
  const rows = fieldset.querySelectorAll('fieldset.window');
  if (rows.length === 0 || rows.length >= MAX_ROWS) return false;
  rows[rows.length - 1].after(blankRow(rows[0]));
  return true;
}

/**
 * Apply a pressed Remove or Add window button in place.
 * @param {Element} fieldset The Show automatically fieldset.
 * @param {HTMLButtonElement} button The button pressed.
 * @returns {boolean} Whether the rows changed.
 */
function changedBy(fieldset, button) {
  if (button.name === 'remove_window') {
    button.closest('fieldset.window').remove();
    return true;
  }
  return button.name === 'add_window' && addRow(fieldset);
}

/**
 * Add and remove window rows without a round trip, then renumber them.
 * Without rows to copy, Add window submits as it does without script.
 * @param {Element} fieldset The Show automatically fieldset.
 * @returns {void}
 */
export function wireWindows(fieldset) {
  fieldset.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || !changedBy(fieldset, button)) return;
    event.preventDefault();
    renumberAll(fieldset);
  });
}

/**
 * Wire whatever this page has.
 * @returns {void}
 */
function start() {
  const list = document.querySelector('ol.pins');
  const order = document.querySelector('#order-form');
  const editor = document.querySelector('form.editor');
  const dialog = document.querySelector('#delete-dialog');
  if (list && order) wireDrag(list, order);
  const windows = document.querySelector('fieldset.windows');
  if (editor) wireEditor(editor);
  if (windows) wireWindows(windows);
  if (dialog && typeof dialog.showModal === 'function') wireDelete(dialog);
}

start();
