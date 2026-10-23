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
  dialog.returnValue = 'cancel';
  dialog.addEventListener('close', () => {
    if (dialog.returnValue !== 'delete') return;
    form.insertAdjacentHTML(
      'beforeend', '<input type="hidden" name="confirm" value="yes">',
    );
    form.submit();
  }, { once: true });
  dialog.showModal();
}

const dialog = document.querySelector('#delete-dialog');
if (dialog && typeof dialog.showModal === 'function') {
  for (const button of document.querySelectorAll('.pins [data-label]')) {
    button.form.addEventListener('submit', (event) => {
      event.preventDefault();
      askToDelete(dialog, button.form, button.dataset.label);
    });
  }
}
