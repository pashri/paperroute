const SUGGEST_URL = '/pins/ways/suggest';
const FAILURE = "Trip Planner didn't answer. Try again in a minute.";

/**
 * Build the address that asks for the suggestion list alone.
 * @param {HTMLFormElement} form The ways page's form.
 * @param {string} page `earlier`, `later` or '' for the search typed.
 * @returns {string} The GET address with `part=suggestions`.
 */
export function suggestionsUrl(form, page = '') {
  const params = new URLSearchParams(new FormData(form));
  params.set('part', 'suggestions');
  if (page) {
    params.set('page', page);
  }
  return `${SUGGEST_URL}?${params}`;
}

/**
 * Fetch the suggestion list and swap it in, or show the failure line.
 * The form's inputs are never changed.
 * @param {HTMLFormElement} form The ways page's form.
 * @param {string} page `earlier`, `later` or '' for the search typed.
 * @returns {Promise<void>} Settles once the list is replaced.
 */
export async function loadSuggestions(form, page = '') {
  const list = form.querySelector('[data-suggestions]');
  try {
    const response = await fetch(suggestionsUrl(form, page));
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    list.outerHTML = await response.text();
  } catch {
    list.innerHTML = `<p class="failure">${FAILURE}</p>`;
  }
}

/**
 * Load suggestions in place when Suggest, Earlier or Later is pressed.
 * @param {HTMLFormElement} form A form holding `[data-suggestions]`.
 * @returns {void}
 */
export function followSuggest(form) {
  form.addEventListener('click', (event) => {
    const button = event.target.closest('[data-suggest], [data-page]');
    if (button) {
      event.preventDefault();
      loadSuggestions(form, 'page' in button.dataset ? button.value : '');
    }
  });
}

if (typeof document !== 'undefined') {
  document
    .querySelectorAll('[data-suggestions]')
    .forEach((list) => followSuggest(list.closest('form')));
}
