/** Save each Settings card as soon as one of its fields changes. */
const NOTES = {
  ok: 'Saved',
  refused: 'Not saved',
  network: 'Not saved: network error',
};

/**
 * Say what a reply to a card's post means.
 * @param {Response} reply - The server's reply.
 * @returns {Promise<string>} The note text.
 */
async function noteFor(reply) {
  if (reply.status === 204) return NOTES.ok;
  if (reply.status === 422) return (await reply.json()).error;
  return NOTES.refused;
}

/**
 * Post a card's fields as JSON and say how it went.
 * @param {HTMLFormElement} form - The card's form.
 * @returns {Promise<string>} The note text.
 */
async function postForm(form) {
  const body = Object.fromEntries(new FormData(form));
  try {
    const reply = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return await noteFor(reply);
  } catch {
    return NOTES.network;
  }
}

/**
 * Save a card, or mark it dirty while its last post is in flight.
 * A dirty card posts again when the reply settles, with its fields as
 * they are then; only the reply to the latest post sets the note.
 * @param {HTMLFormElement} form - The card's form.
 * @param {HTMLElement} note - The card's note.
 * @param {{busy: boolean, dirty: boolean}} card - The card's post state.
 * @returns {Promise<void>}
 */
async function save(form, note, card) {
  if (card.busy) {
    card.dirty = true;
    return;
  }
  card.busy = true;
  do {
    card.dirty = false;
    note.textContent = '';
    const text = await postForm(form);
    if (!card.dirty) note.textContent = text;
    if (text === NOTES.ok) form.querySelector('.error')?.remove();
  } while (card.dirty);
  card.busy = false;
}

/**
 * Hide a card's Save button and save the card on every change.
 * @param {HTMLFormElement} form - The card's form.
 * @returns {void}
 */
function autoSave(form) {
  const note = form.querySelector('.saved');
  const card = { busy: false, dirty: false };
  form.querySelector('button[type="submit"]').hidden = true;
  form.addEventListener('change', () => save(form, note, card));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    save(form, note, card);
  });
}

document.querySelectorAll('.setting form').forEach(autoSave);
