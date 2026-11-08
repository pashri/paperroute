const ALL_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const WEEKDAYS = ALL_DAYS.slice(0, 5);
const WEEKENDS = ALL_DAYS.slice(5);

/**
 * Say whether two day lists hold the same days.
 * @param {string[]} days The days ticked, in week order.
 * @param {string[]} wanted The days to compare with.
 * @returns {boolean} True when they are the same.
 */
function sameDays(days, wanted) {
  return days.join() === wanted.join();
}

/**
 * Name the days ticked.
 * @param {string[]} days Day names such as `mon`, in any order.
 * @returns {string} `Every day`, `Weekdays`, `Weekends` or the names.
 */
function dayText(days) {
  const ordered = ALL_DAYS.filter((day) => days.includes(day));
  if (sameDays(ordered, ALL_DAYS)) return 'Every day';
  if (sameDays(ordered, WEEKDAYS)) return 'Weekdays';
  if (sameDays(ordered, WEEKENDS)) return 'Weekends';
  return ordered
    .map((day) => day.charAt(0).toUpperCase() + day.slice(1))
    .join(', ');
}

/**
 * Describe one window in words.
 * @param {string[]} days The days ticked, as `mon` to `sun`.
 * @param {string} start The From time as `HH:MM`, or empty.
 * @param {string} end The To time as `HH:MM`, or empty.
 * @param {{noDay: string, noTime: string, sameTime: string}} [copy] The
 *   server's refusal texts, shown when the window is not complete.
 * @returns {string} The summary, or the refusal text.
 */
export function windowSummary(days, start, end, copy = {}) {
  if (days.length === 0) return copy.noDay ?? '';
  if (!start || !end) return copy.noTime ?? '';
  if (start === end) return copy.sameTime ?? '';
  const next = end < start ? ' (ends next day)' : '';
  return `Shows ${dayText(days)}, ${start} to ${end}${next}`;
}

/**
 * Describe the window a row holds.
 * @param {Element} row One window row.
 * @param {DOMStringMap} copy The fieldset's data attributes.
 * @returns {string} The row's summary.
 */
function rowSummary(row, copy) {
  const days = [...row.querySelectorAll('input[type="checkbox"]')]
    .filter((box) => box.checked)
    .map((box) => box.value);
  const [start, end] = [...row.querySelectorAll('input[type="time"]')]
    .map((field) => field.value);
  return windowSummary(days, start, end, {
    noDay: copy.noDay,
    noTime: copy.noTime,
    sameTime: copy.sameTime,
  });
}

/**
 * Rewrite every row's preview from what is typed.
 * @param {HTMLFieldSetElement} fieldset The Show automatically fieldset.
 * @returns {void}
 */
function refresh(fieldset) {
  fieldset.querySelectorAll('fieldset.window').forEach((row) => {
    row.querySelector('output.window-preview').textContent = rowSummary(
      row, fieldset.dataset,
    );
  });
}

/**
 * Keep each window's preview line up to date as the form changes.
 * @param {HTMLFieldSetElement} fieldset The Show automatically fieldset.
 * @returns {void}
 */
export function attachWindowPreview(fieldset) {
  const update = () => refresh(fieldset);
  fieldset.addEventListener('input', update);
  fieldset.addEventListener('change', update);
  fieldset.addEventListener('click', update);
  update();
}

if (typeof document !== 'undefined') {
  document
    .querySelectorAll('fieldset[data-window-preview]')
    .forEach(attachWindowPreview);
}
