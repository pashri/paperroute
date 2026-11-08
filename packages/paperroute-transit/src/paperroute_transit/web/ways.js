/**
 * Build the address of a step form's choices, asking only for its steps.
 * @param {HTMLFormElement} form The hand way editor's form.
 * @returns {string} The GET address with `part=steps`.
 */
export function stepsUrl(form) {
  const params = new URLSearchParams(new FormData(form));
  params.set('part', 'steps');
  return `${form.getAttribute('action')}?${params}`;
}

/**
 * Fetch the next steps for the choices made and swap them in place.
 * @param {HTMLFormElement} form The hand way editor's form.
 * @returns {Promise<void>} Settles once the steps are replaced, or left
 *   as they were when the fetch fails.
 */
export async function nextStep(form) {
  const response = await fetch(stepsUrl(form));
  if (!response.ok) {
    return;
  }
  const steps = form.querySelector('fieldset.steps');
  steps.outerHTML = await response.text();
}

/**
 * Follow step choices in a form, fetching the next steps on each one.
 * @param {HTMLFormElement} form A `form[data-steps]`.
 * @returns {void}
 */
export function followSteps(form) {
  form.addEventListener('change', (event) => {
    if (event.target.matches('fieldset.steps select, fieldset.steps input')) {
      nextStep(form).catch(() => {});
    }
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('form[data-steps]').forEach(followSteps);
}
