const MIN_QUERY = 2;
const SEARCH_URL = '/pins/stops.json';

/**
 * Turn a search match into a Tom Select option.
 * @param {{id: string, name: string, routes: string[]}} match One entry
 *   of the stops JSON.
 * @returns {{value: string, text: string, routes: string[]}} The option.
 */
export function stopOption(match) {
  return { value: match.id, text: match.name, routes: match.routes };
}

/**
 * Draw one stop: its name, then a line of route chips.
 * @param {{text: string, routes: string[]}} item The option.
 * @param {(text: string) => string} escape Escapes text for HTML.
 * @returns {string} The markup.
 */
export function renderStop(item, escape) {
  const chips = item.routes
    .map((route) => `<span class="chip">${escape(route)}</span>`)
    .join('');
  return (
    `<div><b>${escape(item.text)}</b>` +
    `<div class="routes">${chips}</div></div>`
  );
}

/**
 * Fetch the stops that match some text.
 * @param {string} query The text typed.
 * @returns {Promise<object[]>} The matches.
 */
async function fetchStops(query) {
  const url = `${SEARCH_URL}?q=${encodeURIComponent(query)}`;
  const response = await fetch(url);
  return response.ok ? response.json() : [];
}

/**
 * Wrap a select in Tom Select, loading stops from the JSON route.
 * @param {HTMLSelectElement} select A `select[data-stop-search]`.
 * @returns {void}
 */
export function attachStopSearch(select) {
  if (typeof TomSelect === 'undefined') {
    return;
  }
  new TomSelect(select, {
    valueField: 'value',
    labelField: 'text',
    searchField: [],
    maxItems: 1,
    shouldLoad: (query) => query.trim().length >= MIN_QUERY,
    load(query, done) {
      fetchStops(query)
        .then((matches) => {
          this.clearOptions();
          done(matches.map(stopOption));
        })
        .catch(() => done());
    },
    render: {
      option: renderStop,
      item: (item, escape) => `<div>${escape(item.text)}</div>`,
    },
  });
}

if (typeof document !== 'undefined') {
  document
    .querySelectorAll('select[data-stop-search]')
    .forEach(attachStopSearch);
}
