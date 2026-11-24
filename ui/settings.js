const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const SESSION = pageData.session;
const SAVE_FAILED = 'Nastavení se nepodařilo uložit.';
const TIME_STAMP_UNUSABLE = 'Služba časových razítek není správně nastavená – upravte adresu.';
const TYPING_PAUSE_MS = 400;
const SWITCHES = {
  update_checks: document.querySelector('[data-update-checks]'),
  crash_offers: document.querySelector('[data-crash-offers]'),
  support_reminder: document.querySelector('[data-support-reminder]'),
};
const SERVICES = [...document.querySelectorAll('[name="time-stamp-service"]')];
const OWN_FIELDS = document.querySelector('[data-time-stamp-own]');
const URL_FIELD = document.querySelector('[data-time-stamp-url]');
const USER_FIELD = document.querySelector('[data-time-stamp-user]');
let stored = pageData.settings;
let typingTimer = null;

const showError = (key, text) => {
  const line = document.querySelector(`[data-error="${key}"]`);
  line.textContent = text;
  line.hidden = !text;
};
const chosenService = () => SERVICES.find(radio => radio.checked)?.value;
const showOwnFields = () => { OWN_FIELDS.hidden = chosenService() !== 'own'; };
const render = settings => {
  for (const [key, control] of Object.entries(SWITCHES)) control.checked = settings[key];
  for (const radio of SERVICES) radio.checked = radio.value === settings.time_stamp.service;
  URL_FIELD.value = settings.time_stamp.url;
  USER_FIELD.value = settings.time_stamp.user;
  showOwnFields();
  showError('time_stamp', settings.time_stamp.usable ? '' : TIME_STAMP_UNUSABLE);
};
const restore = key => {
  if (SWITCHES[key]) {
    SWITCHES[key].checked = stored[key];
    return;
  }
  for (const radio of SERVICES) radio.checked = radio.value === stored.time_stamp.service;
  URL_FIELD.value = stored.time_stamp.url;
  USER_FIELD.value = stored.time_stamp.user;
  showOwnFields();
};
let queue = Promise.resolve();
const send = async body => {
  const response = await fetch(`/settings?s=${encodeURIComponent(SESSION)}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (!response.ok) return {refusal: await response.text()};
  return {answer: await response.json()};
};
const saveInOrder = async (key, body, keepTyping) => {
  let failed = false;
  try {
    const {answer, refusal} = await send(body);
    if (answer) {
      stored = answer;
      showError(key, '');
    } else {
      failed = true;
      showError(key, refusal || SAVE_FAILED);
    }
  } catch {
    failed = true;
    showError(key, SAVE_FAILED);
  }
  if (failed && !keepTyping) restore(key);
};
const save = (key, body, keepTyping = false) => {
  queue = queue.then(() => saveInOrder(key, body, keepTyping));
  return queue;
};
const timeStampBody = () => ({time_stamp: {service: chosenService(), url: URL_FIELD.value.trim(), user: USER_FIELD.value.trim()}});
const saveTimeStamp = keepTyping => save('time_stamp', timeStampBody(), keepTyping);

for (const [key, control] of Object.entries(SWITCHES)) {
  control.addEventListener('change', () => save(key, {[key]: control.checked}));
}
for (const radio of SERVICES) {
  radio.addEventListener('change', () => {
    clearTimeout(typingTimer);
    showOwnFields();
    if (chosenService() === 'own' && !URL_FIELD.value.trim()) return;
    saveTimeStamp(false);
  });
}
for (const field of [URL_FIELD, USER_FIELD]) {
  field.addEventListener('input', () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => saveTimeStamp(true), TYPING_PAUSE_MS);
  });
}
render(stored);
