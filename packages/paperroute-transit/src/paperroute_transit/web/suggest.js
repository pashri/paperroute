/**
 * Build the address that asks for the suggestion list alone.
 * @param {HTMLFormElement} form The ways page's form, whose
 *   `[data-suggestions]` list names the endpoint in `data-suggest-url`.
 * @param {string} page `earlier`, `later` or '' for the search typed.
 * @returns {string} The GET address with `part=suggestions`.
 */
export function suggestionsUrl(form, page = '') {
  const list = form.querySelector('[data-suggestions]');
  const params = new URLSearchParams(new FormData(form));
  params.set('part', 'suggestions');
  if (page) {
    params.set('page', page);
  }
  return `${list.dataset.suggestUrl}?${params}`;
}

/**
 * Replace a list's contents with its failure line, as plain text.
 * @param {HTMLElement} list The `[data-suggestions]` list, whose
 *   `data-failure` holds the line.
 * @returns {void}
 */
function showFailure(list) {
  const line = document.createElement('p');
  line.className = 'failure';
  line.textContent = list.dataset.failure;
  list.replaceChildren(line);
}

/**
 * Fetch the suggestion list and swap it in, or show the list's failure
 * line. A list with no `data-suggest-url` fetches nothing and shows the
 * failure line. The form's inputs are never changed.
 * @param {HTMLFormElement} form The ways page's form.
 * @param {string} page `earlier`, `later` or '' for the search typed.
 * @returns {Promise<void>} Settles once the list is replaced.
 */
export async function loadSuggestions(form, page = '') {
  const list = form.querySelector('[data-suggestions]');
  if (!list.dataset.suggestUrl) {
    showFailure(list);
    return;
  }
  try {
    const response = await fetch(suggestionsUrl(form, page));
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    list.outerHTML = await response.text();
  } catch {
    showFailure(list);
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
