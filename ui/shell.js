(() => {
  const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
  const SESSION = pageData.session;
  const FILE = pageData.file;
  const native = window.zfoNative;
  const queryOne = selector => document.querySelector(selector);
  const status = queryOne('[data-status]');
  const setStatus = text => { status.textContent = text; };
  const setStatusMarkup = markup => { status.innerHTML = markup; };

  const chrome = [...document.querySelectorAll('.zfo-bar, .zfo-help-panel, .zfo-stage, .zfo-panel, .zfo-status, .zfo-dialog, .zfo-notice')];
  const page = queryOne('.zfo-page');
  for (const node of [...document.body.childNodes]) {
    if (!chrome.includes(node) && node.nodeName !== 'STYLE' && !(node.nodeName === 'SCRIPT' && node.hasAttribute('data-zfo-shell'))) {
      page.append(node);
    }
  }
  const BUTTON_OF_THE_FORM = 'button[zfo-app-event]:not(.zfo-picture-button, [zfo-app-event*="addsect"], [zfo-app-event*="delsect"])';
  const drawsWholeFrame = style => ['Top', 'Right', 'Bottom', 'Left']
    .every(side => style[`border${side}Style`] !== 'none' && parseFloat(style[`border${side}Width`]) > 0);
  for (const button of page.querySelectorAll(BUTTON_OF_THE_FORM)) {
    if (drawsWholeFrame(getComputedStyle(button.parentElement))) button.parentElement.classList.add('zfo-button-frame');
  }
  document.body.classList.add('zfo-app');
  document.title = '';
  queryOne('[data-file]').textContent = FILE;
  if (native) queryOne('[data-native]').hidden = false;

  if (pageData.empty) {
    document.body.classList.add('zfo-empty', 'help-hidden', 'panel-hidden');
    for (const button of document.querySelectorAll('.zfo-bar button:not([data-action="open"])')) button.disabled = true;
    queryOne('.zfo-stage').insertAdjacentHTML('beforeend', `<div class="zfo-empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 10h6M9 13h6M9 16h4"/></svg>
      <h1>Žádný formulář není otevřený</h1>
      <div>Otevřete formulář 602XML (.zfo, .fo) nebo dokument PDF k vyplnění a podpisu.</div>
      <button class="zfo-btn primary" data-action="open">Otevřít formulář…</button>
      <div class="zfo-recent" data-recent hidden><h2>Naposledy otevřené</h2></div>
      <button class="zfo-link zfo-support-link" data-action="support">Blanketa je zdarma – podpořit její vývoj</button></div>`);
    const escapeHtmlText = text => text.replace(/[&<>]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[character]));
    window.zfoRecent = paths => {
      const list = queryOne('[data-recent]');
      for (const path of paths) {
        const item = document.createElement('button');
        item.dataset.path = path;
        item.title = path;
        item.innerHTML = `<b>${escapeHtmlText(path.split('/').pop())}</b><span>${escapeHtmlText(path.split('/').slice(0, -1).join('/').replace(/^\/Users\/[^/]+/, '~'))}</span>`;
        list.append(item);
      }
      list.hidden = !paths.length;
    };
    native?.postMessage({action: 'recent'});
    queryOne('[data-file]').textContent = 'Bez formuláře';
    setStatus('Připraveno');
    const open = () => native?.postMessage({action: 'open'});
    document.addEventListener('click', event => {
      if (event.target.closest('[data-action="open"]')) open();
      if (event.target.closest('[data-action="support"]')) zfoSupport();
      const recent = event.target.closest('[data-path]');
      if (recent) native?.postMessage({action: 'openFile', path: recent.dataset.path});
    });
    window.zfoAction = name => { if (name === 'open') open(); else if (name === 'support') zfoSupport(); };
    return;
  }

  let queue = Promise.resolve();
  let changed = false;
  let ready = false;
  const noteChange = () => {
    if (changed || !ready) return;
    changed = true;
    renderSignatures();
  };
  const opening = {populating: false, after: []};
  // The page stays out of layout until the form is complete: the user never sees it half built, and the form scripts'
  // many small changes do not each lay out thousands of fields again. Everything that measures it runs after finishOpening.
  // Meanwhile only a problem reaches the status bar; the engine's "form ready" and "value stored" would say otherwise.
  document.body.classList.add('zfo-opening');
  const OPENING_STATUS = status.textContent;
  const INFO_MESSAGE = 'info';
  const finishOpening = () => {
    document.body.classList.remove('zfo-opening');
    if (status.textContent === OPENING_STATUS) setStatus('Formulář je připraven');
    ready = true;
    fitPage();
    autoZoom();
  };
  // zfo-editor looks every field up with $("*[name=…]"), which walks all elements of the page, and code lists can hold
  // over 100 000 options. Same rules as its get_html_elem, but candidates come from an index of names, rebuilt only when
  // the page changed. takeRecords notices a change made a moment ago, in the same task, too.
  let elementsByName = null;
  const pageChanges = new MutationObserver(() => { elementsByName = null; });
  pageChanges.observe(page, {childList: true, subtree: true, attributes: true, attributeFilter: ['name']});
  const elementsNamed = name => {
    if (pageChanges.takeRecords().length) elementsByName = null;
    if (!elementsByName) {
      elementsByName = new Map();
      for (const element of page.querySelectorAll('[name]')) {
        const key = element.getAttribute('name');
        elementsByName.has(key) ? elementsByName.get(key).push(element) : elementsByName.set(key, [element]);
      }
    }
    return elementsByName.get(name) ?? [];
  };
  const sectionDepth = element => {
    let depth = 0;
    for (let node = element?.parentElement; node; node = node.parentElement) {
      if (node.getAttribute('class')?.startsWith('602-repetition-')) depth++;
    }
    return depth;
  };
  window.get_html_elem = (parent, name, shouldBeInput) => {
    const scope = parent == null ? null : $(parent)[0];
    if (parent != null && !scope) return null;
    const allowedDepth = sectionDepth(scope) + 1;
    for (const element of elementsNamed(name)) {
      if (scope && (scope === element || !scope.contains(element))) continue;
      if (shouldBeInput && /(^|\s)602-repetition-(row|block|table)(\s|$)/.test(element.className)) continue;
      if (sectionDepth(element) <= allowedDepth) return element;
    }
    return null;
  };
  // A browser groups radio buttons by name, and every copy of a repeating section repeats the names: without a form of
  // its own, choosing in one copy cleared the others. Copies cloned on the page carry the form of their original along.
  const SECTION_COPY = '[class~="602-repetition-row"], [class~="602-repetition-block"], [class~="602-repetition-table"]';
  const radioForms = document.createElement('div');
  radioForms.hidden = true;
  document.body.append(radioForms);
  const radioFormOfCopy = new WeakMap();
  const separateRadioGroups = () => {
    for (const radio of page.querySelectorAll('input[type=radio]')) {
      const copy = radio.closest(SECTION_COPY);
      if (!copy) continue;
      if (!radioFormOfCopy.has(copy)) {
        const form = document.createElement('form');
        form.id = `zfo-radios-${radioForms.childElementCount}`;
        radioForms.append(form);
        radioFormOfCopy.set(copy, form.id);
      }
      if (radio.getAttribute('form') !== radioFormOfCopy.get(copy)) radio.setAttribute('form', radioFormOfCopy.get(copy));
    }
  };
  const radioGroupOf = radio => elementsNamed(radio.name).filter(other => other.type === 'radio' && other.form === radio.form);
  const chooseRadio = (radio, value) => radioGroupOf(radio).forEach(other => { other.checked = other.value === value; });
  // The form stores dates, times and numbers the XML way (1990-02-01, 08:30:00, 1234.5) and shows them by the field's
  // Windows picture in form602format (d.M.yyyy, H:mm, # ##0,00), as Form Filler does. What the user types goes back to
  // the stored form; a text that is no date, time or number stays as typed and the form check names it.
  const pad = number => String(number).padStart(2, '0');
  const formatDate = (value, picture) => {
    const match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value);
    if (!picture || !match || /MMM|ddd/.test(picture)) return value;
    const [year, month, day] = match.slice(1).map(Number);
    const parts = {yyyy: String(year), yy: pad(year % 100), MM: pad(month), M: String(month), dd: pad(day), d: String(day)};
    return picture.replace(/yyyy|yy|MM|M|dd|d/g, token => parts[token]);
  };
  const storedDate = text => {
    const typed = /^(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})\.?$/.exec(text.trim());
    const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(text.trim());
    if (!typed && !iso) return text;
    const [day, month, year] = (typed ? typed.slice(1) : iso.slice(1).reverse()).map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? `${year}-${pad(month)}-${pad(day)}` : text;
  };
  const formatTime = (value, picture) => {
    const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?$/.exec(value);
    if (!picture || !match || /[ht]/.test(picture)) return value;
    const [hours, minutes, seconds = 0] = match.slice(1).map(part => Number(part ?? 0));
    const parts = {HH: pad(hours), H: String(hours), mm: pad(minutes), m: String(minutes), ss: pad(seconds), s: String(seconds)};
    return picture.replace(/HH|H|mm|m|ss|s/g, token => parts[token]);
  };
  const storedTime = text => {
    const match = /^(\d{1,2})\s*[:.]\s*(\d{2})(?:\s*[:.]\s*(\d{2}))?$/.exec(text.trim());
    if (!match) return text;
    const [hours, minutes, seconds = 0] = match.slice(1).map(part => Number(part ?? 0));
    return hours < 24 && minutes < 60 && seconds < 60 ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}` : text;
  };
  const numberPicture = picture => {
    const match = /^([^#0]*)([#0](?:[#0 ]*[#0])?)(?:,([#0]+))?(.*)$/.exec(picture ?? '');
    if (!match) return null;
    const [, quotedPrefix, whole, decimals = '', quotedSuffix] = match;
    const [prefix, suffix] = [quotedPrefix, quotedSuffix].map(text => text.replace(/'([^']*)'/g, '$1'));
    return {prefix, suffix, grouped: whole.includes(' '), fewest: decimals.split('0').length - 1, most: decimals.length};
  };
  // Rounds the decimal digits as written, half up: binary floating point would show 2.675 as 2,67.
  const roundDecimals = (whole, fraction, places) => {
    const digits = BigInt(whole + fraction.slice(0, places)) + (fraction[places] >= '5' ? 1n : 0n);
    const text = digits.toString().padStart(places + 1, '0');
    return [text.slice(0, text.length - places), text.slice(text.length - places)];
  };
  // A field shows every digit it holds, an output rounds to the picture as Form Filler prints it.
  const formatNumber = (value, picture, rounded) => {
    const parts = numberPicture(picture);
    const match = /^([-+]?)(\d*)(?:\.(\d*))?$/.exec(value);
    if (!parts || !match || !/\d/.test(value)) return value;
    let [, sign, whole, fraction = ''] = match;
    if (rounded && fraction.length > parts.most) [whole, fraction] = roundDecimals(whole, fraction, parts.most);
    fraction = fraction.replace(/0+$/, '').padEnd(parts.fewest, '0');
    whole = (whole.replace(/^0+(?=\d)/, '') || '0');
    if (parts.grouped) whole = whole.replace(/\B(?=(\d{3})+$)/g, '\u00a0');
    const negative = sign === '-' && /[1-9]/.test(whole + fraction);
    return `${parts.prefix}${negative ? '-' : ''}${whole}${fraction ? ',' + fraction : ''}${parts.suffix}`;
  };
  const storedNumber = (text, picture) => {
    const parts = numberPicture(picture) ?? {prefix: '', suffix: ''};
    let plain = text.trim();
    if (parts.prefix.trim() && plain.startsWith(parts.prefix.trim())) plain = plain.slice(parts.prefix.trim().length);
    if (parts.suffix.trim() && plain.endsWith(parts.suffix.trim())) plain = plain.slice(0, -parts.suffix.trim().length);
    plain = plain.replace(/[\s\u00a0]/g, '').replace(',', '.');
    return /^[-+]?\d*\.?\d*$/.test(plain) && /\d/.test(plain) ? plain : text;
  };
  const fieldKind = element => {
    const picture = element.dataset?.format ?? '';
    if (element.classList.contains('date-pick')) return 'date';
    if (element.classList.contains('timeInput')) return 'time';
    if (element.classList.contains('numberInput')) return 'number';
    if (!element.classList.contains('outputelement') || !picture) return '';
    return /[#0]/.test(picture) ? 'number' : /[Hhms]/.test(picture) && !/[yd]/.test(picture) ? 'time' : /[yMd]/.test(picture) ? 'date' : '';
  };
  // Form Filler takes 00.0 in a number field for no value (spfillcz.xsl reads it out as "nevyplněno").
  const UNFILLED_NUMBER = '00.0';
  const shownValue = (element, value) => {
    const picture = element.dataset?.format ?? '';
    const text = value === UNFILLED_NUMBER && element.classList.contains('numberInput') ? '' : value ?? '';
    return {date: () => formatDate(text, picture), time: () => formatTime(text, picture),
            number: () => formatNumber(text, picture, element.classList.contains('outputelement'))}[fieldKind(element)]?.() ?? text;
  };
  const storedValue = (element, text) => ({date: () => storedDate(text), time: () => storedTime(text),
                                           number: () => storedNumber(text, element.dataset?.format)}[fieldKind(element)]?.() ?? text);
  const setValue = window.set_input_value;
  window.set_input_value = (input, value) => {
    const element = $(input)[0];
    if (!element) return;
    if (element.classList.contains('outputelement')) element.textContent = shownValue(element, value);
    else setValue(input, shownValue(element, value));
  };
  const setInitialValues = window.set_inputs_init_values_callback;
  window.set_inputs_init_values_callback = data => {
    for (const [id, item] of Object.entries(data)) {
      const output = document.getElementById(id);
      if (!output?.classList.contains('outputelement')) continue;
      set_input_value(output, item.value);
      delete data[id];
    }
    setInitialValues(data);
  };
  // jQuery UI writes and reads the date by its own letters: d, dd, m, mm, y and yy stand for d, dd, M, MM, yy and yyyy.
  const pickerFormat = picture => (!picture || /MMM|ddd/.test(picture) ? 'yy-mm-dd'
    : picture.replace(/yyyy|yy|MM|M|dd|d/g, token => ({yyyy: 'yy', yy: 'y', MM: 'mm', M: 'm', dd: 'dd', d: 'd'})[token]));
  const prepareDates = window.prepare_date_inputs;
  window.prepare_date_inputs = context => {
    prepareDates(context);
    $('.date-pick', context ?? undefined).each(function () { $(this).datepicker('option', 'dateFormat', pickerFormat(this.dataset.format)); });
  };
  window.number_input_keypress_handler = () => {};
  // Check boxes of one bind write their own values to it (form602on, form602off or empty when unchecked): together they
  // make one choice, within one copy of a section. The engine answers with the value of the field, not with on or off.
  const checkboxGroupOf = box => elementsNamed(box.name)
    .filter(other => other.type === 'checkbox' && other.closest(SECTION_COPY) === box.closest(SECTION_COPY));
  const checkBoxes = (box, value) => checkboxGroupOf(box).forEach(other => { other.checked = other.value === value; });
  window.set_checkbox_value = (box, value) => checkBoxes($(box)[0], value);
  const storeFieldChange = window.field_changed_handler;
  window.field_changed_handler = field => {
    if (field.type !== 'checkbox' && !fieldKind(field)) { storeFieldChange(field); return; }
    const value = field.type === 'checkbox' ? (field.checked ? field.value : (field.dataset.off ?? '')) : storedValue(field, field.value);
    if (field.type === 'checkbox') checkBoxes(field, value);
    else field.value = shownValue(field, value);
    gtk_handler_start();
    send(JSON.stringify({type: 'store_val', data: {field: field.name, value, repe: get_parent_repe(field), input_id: field.id}}));
  };
  // Its set_radio_value found the button by an id that every copy of a section repeats and never cleared a choice. Its
  // callers pass only the name, so the value goes to the first group, the one outside sections or in the first copy.
  window.set_radio_value = (name, value) => {
    const first = elementsNamed(name).find(element => element.type === 'radio');
    if (first) chooseRadio(first, value);
  };
  // A field that became a list once Blanketa got its code list (codelists.py) can hold a text typed in before: it stays
  // shown as a choice of its own, else the field would look empty while the form keeps the value.
  const keepingValueOutsideTheList = (input, value) => {
    if (input.tagName === 'SELECT' && value && ![...input.options].some(option => option.value === value || option.text === value)) {
      input.append(new Option(value, value));
    }
    return input;
  };
  // Its fill_inputs also counted each section's copies through a full walk of the page and never used the number.
  window.fill_inputs = (parent, data) => {
    if (parent == null) separateRadioGroups();
    for (const [name, value] of Object.entries(data)) {
      if (is_array(value)) {
        value.forEach((copy, index) => fill_inputs($(get_html_elem(parent, `${name}-rep-${index}`, false)), copy));
      } else {
        const input = get_html_elem(parent, name, true);
        if (!input || $(input).hasClass('shouldStayEmpty')) continue;
        if (input.type === 'radio') chooseRadio(input, value);
        else set_input_value(keepingValueOutsideTheList(input, value), value);
      }
    }
  };
  // Showing or hiding a part measures the page even when nothing changes, and this runs after every stored value.
  window.set_multiswitch_controls_callback = ({data: switches}) => {
    for (const {bindref, shown, cases} of switches) {
      for (const value of cases) {
        const show = shown.includes(value);
        for (const part of elementsNamed(`div-${bindref}-${value}`)) {
          if (part.tagName === 'DIV' && (part.style.display === 'none') === show) $(part)[show ? 'show' : 'hide']();
        }
      }
    }
    gtk_handler_finished();
  };
  // This pass goes through every field of the page and runs after every stored value, so it is skipped when the answer
  // and the page are the same as last time.
  const applyReadonly = window.set_readonly_inputs_callback;
  let readonlyApplied = null;
  window.set_readonly_inputs_callback = data => {
    const names = JSON.stringify(data.readonly_names);
    elementsNamed('');
    if (readonlyApplied?.names === names && readonlyApplied.page === elementsByName) { gtk_handler_finished(); return; }
    applyReadonly(data);
    elementsNamed('');
    readonlyApplied = {names, page: elementsByName};
  };
  // That pass also takes the red off a field whose value was refused, so the next one has to run in full.
  const refuseValue = window.field_changed_error_callback;
  window.field_changed_error_callback = data => { readonlyApplied = null; refuseValue(data); };
  // The engine only keeps a repeating section's HTML to hand it back, and with code lists it runs to megabytes each way.
  // It keeps the section's name instead, and the page swaps that for the HTML in the answers.
  const sectionHtml = new Map();
  window.send_rep_sections_originals = () => {
    const references = {};
    for (const section of page.querySelectorAll('[class^="602-repetition-"]')) {
      const name = section.getAttribute('name');
      if (!name) continue;
      const reference = name.slice(0, name.lastIndexOf('-rep-'));
      sectionHtml.set(reference, section.outerHTML);
      references[name] = reference;
    }
    gtk_handler_start();
    send(JSON.stringify({type: 'set_html_templates', data: references}));
  };
  const startSections = window.init_rep_sections_callback;
  window.init_rep_sections_callback = data => {
    for (const name of Object.keys(data.templates)) data.templates[name] = sectionHtml.get(data.templates[name]);
    startSections(data);
  };
  const addSection = window.server_add_section_callback;
  window.server_add_section_callback = data => {
    addSection({...data, repe_html: sectionHtml.get(data.repe_html)});
    separateRadioGroups();
  };
  window.data_changed_callback = noteChange;
  // The engine answers a form's button only with whether it changed the data, so the page recalculates and reads them all back.
  window.perform_click_action_callback = answer => {
    gtk_handler_finished();
    if (!answer?.changed) return;
    noteChange();
    gtk_handler_start();
    send(JSON.stringify({type: 'recalc_form', data: ''}));
    set_inputs_init_values();
  };
  // Each section copied while opening would bind these two to every field of the page again; once at the end is enough.
  for (const name of ['set_field_info_request_handler', 'set_date_inputs_clear_func']) {
    const original = window[name];
    window[name] = (...args) => { if (!opening.populating) original(...args); };
    opening.after.push(original);
  }
  const populate = window.populate_repeating_sections;
  window.populate_repeating_sections = (...args) => {
    opening.populating = true;
    try { populate(...args); } finally { opening.populating = false; }
    opening.after.forEach(run => run());
  };
  const paper = pageData.page;
  Object.assign(page.style, paper);
  const codeLists = new Set(pageData.codes);
  const showCodes = select => {
    for (const option of select.options) {
      if (option.value && !option.dataset.code) {
        option.dataset.code = option.value;
        option.textContent = option.textContent.trim() && option.textContent.trim() !== option.value
          ? `${option.value} – ${option.textContent.trim()}` : option.value;
      }
    }
  };
  for (const select of page.querySelectorAll('select[name]')) {
    if (!codeLists.has(select.name)) continue;
    showCodes(select);
    new MutationObserver(() => showCodes(select)).observe(select, {childList: true});
  }
  const colors = pageData.colors;
  document.documentElement.style.setProperty('--field', colors.field);
  document.documentElement.style.setProperty('--field-readonly', colors.readonly);
  Object.assign(FIELD_COLORS, {editable: colors.field, readonly: colors.readonly});
  const removeEmptyTrailingRepeatColumns = () => {
    for (const table of page.querySelectorAll('[class~="602-repetition-table"]')) {
      const column = table.querySelector(':scope > colgroup > col:last-child');
      const cells = [...table.querySelectorAll(':scope > tbody > tr')]
        .filter(row => !row.querySelector(':scope > td > button[onclick^="clone_table"]'))
        .map(row => row.lastElementChild);
      if (!column || column.parentElement.children.length < 2 || !cells.length
          || cells.some(cell => cell.childElementCount || cell.textContent.trim() || cell.parentElement.cells.length < 2)) continue;
      // Removed rather than narrowed: WebKit treats a zero-width column as unsized and splits the width with it.
      column.remove();
      cells.forEach(cell => cell.remove());
    }
  };
  const fitPage = () => {
    removeEmptyTrailingRepeatColumns();
    page.style.width = paper.width;
    const style = getComputedStyle(page);
    const contentRight = page.getBoundingClientRect().right - parseFloat(style.paddingRight) * zoom;
    const overflow = Math.max(0, ...[...page.querySelectorAll('table')]
      .filter(table => table.offsetParent)
      .map(table => table.getBoundingClientRect().right - contentRight)) / zoom;
    if (overflow > 1) page.style.width = `calc(${paper.width} + ${Math.ceil(overflow)}px)`;
  };
  new ResizeObserver(() => ready && fitPage()).observe(page);

  page.addEventListener('click', event => {
    const button = event.target.closest('button[zfo-app-event]');
    const appEvent = button?.getAttribute('zfo-app-event') ?? '';
    const add = appEvent.includes('addsect');
    if (!add && !appEvent.includes('delsect')) return;
    event.preventDefault();
    event.stopPropagation();
    const onclickStartsWith = prefix => candidate => (candidate.getAttribute('onclick') ?? '').startsWith(prefix);
    let target = [...button.closest('tr').querySelectorAll('button')].find(onclickStartsWith(add ? 'clone_row' : 'del_row'));
    if (!target) {
      const section = button.closest('[class~="602-repetition-table"]');
      target = section && [...section.querySelectorAll(':scope > tbody > tr > td > button')].find(onclickStartsWith(add ? 'clone_table' : 'del_table'));
    }
    if (!target) {
      const sectionButtonsAbove = [...page.querySelectorAll(`button[onclick^="${add ? 'clone_' : 'del_'}"]`)]
        .filter(candidate => candidate.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING);
      target = sectionButtonsAbove.at(-1);
    }
    target?.click();
  }, true);
  // Whether a field would be visible once the page is shown; jQuery's :visible measures, and the page is not laid out yet.
  const isShownOnceLaidOut = element => {
    for (let node = element; node && node !== page; node = node.parentElement) {
      if (getComputedStyle(node).display === 'none') return false;
    }
    return true;
  };
  // Each answer sends the next message before its task ends, so an empty queue means opening is over. That holds when a
  // step failed too: the user then gets the form as far as it got rather than an empty page.
  const enqueue = task => {
    const log = error => console.error('zfo', error);
    const tail = queue = queue.then(task).catch(log)
      .then(() => { if (queue === tail && !ready) finishOpening(); }).catch(log);
  };
  // These only read the current state back into the page, and every stored value asks for all of them. Only the newest
  // request of each kind needs an answer, so a form storing 50 values refreshes once, not 50 times.
  const REFRESHES = new Set(['get_repeatable', 'get_readonly_binds', 'get_multiswitch_list']);
  const newestRefresh = new Map();
  let messages = 0;
  let refusedValue = null;
  window.send = message => {
    const {type, data} = JSON.parse(message);
    const number = ++messages;
    if (REFRESHES.has(type)) newestRefresh.set(type, number);
    enqueue(async () => {
      if (REFRESHES.has(type) && newestRefresh.get(type) !== number) { gtk_handler_finished(); return; }
      const response = await fetch('/ipc?s=' + SESSION, {method: 'POST', body: message});
      if (!response.ok) { setStatus('Chyba: ' + await response.text()); return; }
      let refused = false;
      for (const answer of await response.json()) {
        if (answer.type === 'check') {
          showCheck(answer.data, answer.scripts);
        } else if (answer.type === 'default') {
          applyDefault(answer.data);
        } else if (answer.type === 'javascript') {
          try { (0, eval)(answer.data); } catch (error) { console.error(error, answer.data.slice(0, 200)); }
        } else if (answer.type === 'gtk' && answer.info_area === 'message_area' && (ready || answer.info_type !== INFO_MESSAGE)) {
          // The message may carry markup and the form's own texts: parsed inertly, only its text is shown.
          setStatus(new DOMParser().parseFromString(String(answer.data?.to_display ?? ''), 'text/html').body.textContent);
          if (type === 'store_val') {
            refused = true;
            refusedValue = {field: data.field, repe: data.repe, status: status.textContent};
          }
        }
      }
      if (type === 'store_val' && !refused && refusedValue?.field === data.field && refusedValue.repe === data.repe) {
        if (status.textContent === refusedValue.status) setStatus('');
        refusedValue = null;
      }
      if (GlobalVars.handlers_active < 0) GlobalVars.handlers_active = 0;
      if (ready) fitPage();
      if (type === 'store_val') noteChange();
      if (type === 'get_multiswitch_list' && !window.selectsSynced) {
        window.selectsSynced = true;
        $('select[name]').each(function () { if (this.value && isShownOnceLaidOut(this)) $(this).trigger('change'); });
        queryOne('[data-cert-slot]').replaceWith(certificates.element);
        queryOne('[data-find-slot]').replaceWith(findInput);
      }
    });
  };

  const plainTextOf = markup => new DOMParser().parseFromString(String(markup), 'text/html').body.textContent;
  const repeatingSectionsNamed = (name, parent) => [...(parent ?? document).querySelectorAll('[name^="' + CSS.escape(name) + '-rep-"]')]
    .filter(section => section.getAttribute('name').slice(name.length + 5).match(/^\d+$/));
  const removeSurplusSections = (sections, parent = null) => {
    for (const [name, copies] of Object.entries(sections)) {
      const drawn = repeatingSectionsNamed(name, parent);
      drawn.slice(copies.length).forEach(section => section.remove());
      copies.forEach((sectionCopy, index) => drawn[index] && removeSurplusSections(sectionCopy.sub_rs, drawn[index]));
    }
  };
  const redrawSections = result => {
    const templates = Object.fromEntries(Object.entries(result.templates).map(([name, reference]) => [name, sectionHtml.get(reference)]));
    populate_repeating_sections(null, {sections: result.sections, templates});
    removeSurplusSections(result.sections);
    identify_inputs(null);
    identify_date_inputs(null);
    prepare_date_inputs(null);
    identify_repe_sections();
  };
  const focusField = (bind, isInNewSection) => {
    const fields = [...document.getElementsByName(bind)].filter(candidate => candidate.offsetParent);
    const field = isInNewSection ? fields.at(-1) : fields[0];
    field?.scrollIntoView({block: 'center'});
    field?.focus();
  };
  const registerUnreachable = {error: 'Registru se nepodařilo položit dotaz. Údaje vyplňte ručně.'};
  const askRegister = async ({url, question, retry}) => {
    if (!confirm(question)) {
      setStatus('Údaje z registru se nedoplnily, dotaz jste nepotvrdili. Zkontrolujte název a adresu firmy.');
      return;
    }
    setStatus('Ptám se registru…');
    const answer = await fetch(`/lookup?s=${SESSION}`, {method: 'POST', body: JSON.stringify({url})})
      .then(response => response.ok ? response.json() : registerUnreachable)
      .catch(() => registerUnreachable);
    if (answer.error) {
      alert(answer.error);
      setStatus(answer.error);
      return;
    }
    setStatus('');
    send(JSON.stringify({type: 'run_form_script', data: {...retry, lookups: {...retry.lookups, [url]: answer}}}));
  };
  const addressesNotChecked = 'Adresy nebyly ověřeny v\u00a0registru RÚIAN. Před odesláním je zkontrolujte.';
  const addressVerdict = ({address, verdict, registered}) => ({
    known: null,
    similar: `Adresu „${address}“ registr nezná. Zná: ${registered.map(text => `„${text}“`).join(', ')}.`,
    unknown: `Adresu „${address}“ registr nezná.`,
  })[verdict];
  const checkAddresses = async addresses => {
    const question = 'Formulář chce ověřit adresy v\u00a0registru RÚIAN.\n\nBlanketa pošle Českému úřadu zeměměřickému a\u00a0katastrálnímu '
      + `(ags.cuzk.gov.cz) tyto adresy:\n${addresses.map(address => `• ${address}`).join('\n')}\n\nNic jiného z\u00a0formuláře neodejde. Poslat?`;
    if (!confirm(question)) {
      setStatus(addressesNotChecked);
      return;
    }
    setStatus('Ověřuji adresy v\u00a0registru RÚIAN…');
    const answer = await fetch(`/check-addresses?s=${SESSION}`, {method: 'POST', body: '{}'})
      .then(response => response.ok ? response.json() : {error: addressesNotChecked})
      .catch(() => ({error: addressesNotChecked}));
    if (answer.error) {
      alert(answer.error);
      setStatus(answer.error);
      return;
    }
    const problems = answer.results.map(addressVerdict).filter(Boolean);
    if (!problems.length) {
      setStatus('Registr RÚIAN adresy zná.');
      return;
    }
    alert(`${problems.join('\n\n')}\n\nOpravte adresu podle registru, nebo ji zkontrolujte. Úřad může podání s\u00a0adresou, kterou registr nezná, odmítnout.`);
    const count = problems.length;
    setStatus(count === 1 ? 'Jednu adresu registr RÚIAN nezná.' : `Registr RÚIAN nezná ${count}\u00a0${count < 5 ? 'adresy' : 'adres'}.`);
  };
  const offerNavigation = () => {
    const anchors = [...page.querySelectorAll('[data-anchor]')].filter(anchor => anchor.offsetParent);
    if (!anchors.length) { setStatus('Formulář nemá části, na které by šlo přejít.'); return; }
    const dialog = queryOne('[data-navigation]');
    const list = dialog.querySelector('[data-navigation-list]');
    list.replaceChildren(...anchors.map(anchor => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'zfo-btn';
      item.textContent = anchor.dataset.anchor;
      // Closing gives the focus back to the button that opened the dialog, so the part is reached only after that.
      item.addEventListener('click', () => {
        dialog.close();
        goToPart(anchor);
      });
      return item;
    }));
    dialog.showModal();
  };
  // A part of the form is mostly its heading alone: its fields follow it in the page, up to the next part. A part whose
  // fields are all hidden takes the focus itself, so the next Tab still starts there.
  const isAfter = (node, other) => Boolean(node.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING);
  const goToPart = anchor => {
    const nextPart = [...page.querySelectorAll('[data-anchor]')].find(other => !anchor.contains(other) && isAfter(anchor, other));
    const field = [...page.querySelectorAll('input:not([type="hidden"]), select, textarea')]
      .find(candidate => candidate.offsetParent && !candidate.disabled
        && (anchor.contains(candidate) || isAfter(anchor, candidate)) && !(nextPart && isAfter(nextPart, candidate)));
    if (!field) anchor.tabIndex = -1;
    (field ?? anchor).focus({preventScroll: true});
    anchor.scrollIntoView({block: 'start'});
  };
  const closeWindow = () => {
    if (native) native.postMessage({action: 'close'});
    else setStatus('Formulář chtěl zavřít okno. V\u00a0prohlížeči kartu zavřete sami.');
  };
  window.run_form_button = button => send(JSON.stringify({type: 'run_form_script', data: {button: Number(button.dataset.formButton), repe: get_parent_repe(button)}}));
  window.form_script_callback = result => {
    if (result.question) {
      const {text, title, retry} = result.question;
      const answer = confirm(plainTextOf(title ? `${title}\n\n${text}` : text));
      send(JSON.stringify({type: 'run_form_script', data: {...retry, answers: [...retry.answers, answer]}}));
      return;
    }
    if (result.lookup) {
      askRegister(result.lookup);
      return;
    }
    if (result.stopped) {
      alert(result.stopped);
      setStatus(result.stopped);
      return;
    }
    if (result.refused) {
      setStatus(`Tuto akci formuláře Blanketa neumí provést (${result.refused}), data zůstala beze změny.`);
      return;
    }
    if (result.changed) noteChange();
    if (result.sections) redrawSections(result);
    for (const [bind, value] of Object.entries(result.values)) {
      const fields = [...document.getElementsByName(bind)];
      fields.filter(field => field.type !== 'radio').forEach(field => set_input_value(field, value));
      if (fields.some(field => field.type === 'radio')) set_radio_value(bind, value);
    }
    calc_repeatable_sections();
    set_readonly_inputs();
    set_multiswitch_controls();
    result.messages.map(({text, title}) => plainTextOf(title ? `${title}\n\n${text}` : text).trim()).filter(Boolean).forEach(message => alert(message));
    if (result.notices.length) setStatus(result.notices.join(' '));
    if (result.save) saveXml(result.save.name, pageData.folder, result.save.content);
    if (result.navigate) offerNavigation();
    if (result.print) print();
    if (result.close) closeWindow();
    queue.then(() => {
      if (result.problems?.length) showCheck(result.problems, result.scripts ?? []);
      else if (result.problems) clearCheck({keepStatus: result.notices.length > 0});
      if (result.focus) focusField(result.focus, Boolean(result.sections));
      if (result.addresses?.length) checkAddresses(result.addresses);
    });
  };

  const icons = {
    ok: '<svg viewBox="0 0 24 24" fill="none" stroke="#1e8e3e" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="#b26a00" stroke-width="2"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/></svg>',
    bad: '<svg viewBox="0 0 24 24" fill="none" stroke="#c5221f" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
  };
  const formatDateTime = iso => iso ? new Date(iso).toLocaleString('cs-CZ', {dateStyle: 'medium', timeStyle: 'short'}) : '–';
  const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
  function signatureCard(signature) {
    const expired = new Date(signature.not_after) < new Date();
    const revocation = window.zfoRevocation;
    const level = !signature.intact || revocation.revokedBeforeSigning(signature) ? 'bad'
      : (signature.trusted && !expired && !revocation.revoked(signature)) ? 'ok' : 'warn';
    const state = !signature.intact ? 'Podpis je porušen – obsah byl po podpisu změněn'
      : revocation.state(signature) ? revocation.state(signature)
      : signature.trusted ? 'Podpis je platný, certifikát je důvěryhodný'
      : signature.unverifiable ? 'Obsah je neporušený, důvěryhodnost certifikátu se nepodařilo ověřit'
      : 'Obsah je neporušený, certifikát není hodnocen jako důvěryhodný';
    return `<div class="zfo-card ${level}" title="${escapeHtml(signature.reason || '')}">
      <div class="who">${icons[level]}${escapeHtml(signature.subject)}</div>
      <div class="state">${state}</div>
      ${signature.trust_source === 'eu' ? '<div class="meta">Vydavatel je v důvěryhodném seznamu EU.</div>' : ''}
      ${signature.unverifiable ? `<div class="meta">${escapeHtml(signature.reason)}</div>` : ''}
      ${revocation.line(signature)}
      <div class="meta">Podepsáno: ${formatDateTime(signature.signed_at)}<br>Vystavil: ${escapeHtml(signature.issuer)}<br>
        ${expired ? 'Platnost certifikátu skončila' : 'Certifikát platí do'} ${new Date(signature.not_after).toLocaleDateString('cs-CZ')}</div>
    </div>`;
  }
  let signatures = pageData.signatures;
  function renderSignatures() {
    const dataSignatures = signatures.filter(signature => !signature.publisher);
    const publication = signatures.find(signature => signature.role === 'publication');
    const license = signatures.findLast(signature => signature.role === 'license');
    queryOne('[data-signatures="data"]').innerHTML =
      (changed && dataSignatures.length ? '<div class="zfo-note">Formulář byl změněn. Stávající podpis se při uložení nahradí novým.</div>' : '') +
      (dataSignatures.length ? dataSignatures.map(signatureCard).join('') : '<div class="zfo-empty">Data formuláře zatím nejsou podepsaná.</div>');
    queryOne('[data-signatures="publisher"]').innerHTML =
      publication ? signatureCard(publication) : '<div class="zfo-empty">Formulář nebyl dosud publikován – nemá podpis vydavatele.</div>';
    queryOne('[data-license-head]').hidden = !license;
    queryOne('[data-signatures="license"]').innerHTML = license ? signatureCard(license) : '';
    window.zfoRevocation.ask(SESSION, signatures, renderSignatures);
  }
  renderSignatures();

  const warnAboutUnverifiedPublisherOncePerSession = () => {
    const publication = signatures.find(signature => signature.role === 'publication');
    const key = 'zfo-publisher-warned:' + FILE;
    let warned = false;
    try { warned = sessionStorage.getItem(key) === '1'; } catch {}
    if (!warned && (!publication || !publication.intact)) {
      const dialog = queryOne('[data-publisher-warning]');
      dialog.querySelector('[data-publisher-warning-text]').textContent = publication
        ? 'Podpis vydavatele formuláře je porušen. Formulář mohl být po vydání změněn. Vyplňujte ho, jen pokud důvěřujete jeho zdroji.'
        : 'Formulář nebyl dosud publikován: nemá podpis vydavatele, takže nelze ověřit, kdo ho vydal a zda nebyl změněn. '
          + 'Vyplňujte ho, jen pokud důvěřujete jeho zdroji.';
      dialog.addEventListener('close', () => { if (dialog.returnValue === 'details') togglePanel('panel', true); }, {once: true});
      try { sessionStorage.setItem(key, '1'); } catch {}
      dialog.showModal();
    }
  };
  warnAboutUnverifiedPublisherOncePerSession();

  // Certificates for signing. The select joins the toolbar only after the form scripts have
  // wired up their own selects, so they do not treat it as a form field.
  const certificates = window.zfoCertificates(SESSION);
  const certSelect = certificates.select;

  const info = pageData.info;
  const formatDay = iso => new Date(iso).toLocaleDateString('cs-CZ');
  queryOne('[data-info]').innerHTML = [
    info.title && `<b>${escapeHtml(info.title)}</b>`,
    info.subject && `Předmět: ${escapeHtml(info.subject)}`,
    info.author && `Autor: ${escapeHtml(info.author)}`,
    info.keywords && `Klíčová slova: ${escapeHtml(info.keywords)}`,
    info.comment && `Komentář: ${escapeHtml(info.comment)}`,
    info.code && `Kód formuláře: ${escapeHtml(info.code)}`,
    info.form_id && `ID formuláře: ${escapeHtml(info.form_id)}`,
    info.created && `Vytvořen: ${formatDay(info.created)}`,
    info.modified && `Modifikován: ${formatDay(info.modified)}`,
    pageData.blank && 'Zobrazeny výchozí hodnoty – vyplněná data nejsou načtena.',
    info.scripts && 'Některé výpočty nebo kontroly v JavaScriptu 602 Blanketa neumí provést.',
    info.actions && 'Skripty spouštěné při nahrání přílohy Blanketa nespouští.',
    info.missing_lists && 'Některé číselníky, které si formulář stahuje z\u00a0internetu, Blanketa nezískala.',
  ].filter(Boolean).join('<br>');
  if (info.scripts || info.actions || info.missing_lists) {
    const notice = queryOne('[data-notice]');
    notice.querySelector('span').textContent = (info.scripts
      ? 'Některé výpočty nebo kontroly v tomto formuláři Blanketa neumí provést, pole proto mohou zůstat prázdná nebo neaktuální. '
      : info.actions ? 'Při nahrání přílohy tento formulář spouští skripty, které Blanketa z\u00a0bezpečnostních důvodů nespouští. '
      : 'Některé číselníky, které si formulář stahuje z\u00a0internetu, se nepodařilo získat. Tyto položky vyplňte ručně. ')
      + 'Před odesláním formulář zkontrolujte, spolehlivý výsledek dá Software602 Form Filler.';
    notice.querySelector('button').addEventListener('click', () => { notice.hidden = true; });
    notice.hidden = false;
  }

  const UNLABELLED_FIELD = 'Pole bez popisku';
  const LABELLED_ON_THE_RIGHT = new Set(['checkbox', 'radio']);
  const nearestText = (cell, step) => {
    for (let other = cell?.[step]; other && !other.querySelector('input, select, textarea'); other = other[step]) {
      if (other.textContent.trim()) return other.textContent;
    }
    return '';
  };
  const labelFor = field => {
    const cell = field.closest('td');
    const above = cell?.parentElement.previousElementSibling?.cells[[...cell.parentElement.cells].indexOf(cell)];
    const labels = [nearestText(cell, 'previousElementSibling'), above?.textContent ?? ''];
    if (LABELLED_ON_THE_RIGHT.has(field.type)) labels.unshift(nearestText(cell, 'nextElementSibling'));
    return labels.find(text => text.trim()) ?? '';
  };
  const shortOneLine = text => {
    const line = text.replace(/\s+/g, ' ').trim();
    return line.length > 90 ? `${line.slice(0, 89)}…` : line;
  };
  const fieldHeading = field => shortOneLine(labelFor(field)) || UNLABELLED_FIELD;
  let checkWaiter = null;
  function clearCheckMarks() {
    page.querySelectorAll('.zfo-invalid').forEach(field => field.classList.remove('zfo-invalid'));
  }
  function clearCheck({keepStatus}) {
    clearCheckMarks();
    const check = queryOne('[data-check]');
    const checkWasShown = !check.hidden;
    queryOne('[data-check-list]').replaceChildren();
    check.hidden = true;
    if (checkWasShown && !keepStatus) setStatus('');
  }
  function showCheck(problems, scripts = []) {
    clearCheckMarks();
    const fieldOf = problem => problem.field ? document.getElementsByName(problem.field)[problem.index] ?? document.getElementsByName(problem.field)[0] : null;
    // The form's own check names its faults in words, they matter even where the field is not on the page.
    const found = problems
      .map(problem => ({...problem, fieldElement: fieldOf(problem)}))
      .map(problem => ({...problem, element: problem.fieldElement?.offsetParent ? problem.fieldElement : null}))
      .filter(problem => problem.element || problem.kind === 'form');
    found.forEach(problem => problem.element?.classList.add('zfo-invalid'));
    const list = queryOne('[data-check-list]');
    list.innerHTML = found.length || scripts.length ? '' : '<div class="zfo-check-ok">Formulář je vyplněn správně.</div>';
    if (scripts.length) {
      const note = document.createElement('div');
      note.textContent = 'Některé výpočty nebo kontroly tohoto formuláře Blanketa neumí provést, kontrola proto není úplná.';
      list.append(note);
    }
    for (const problem of found) {
      const item = document.createElement('button');
      item.className = 'zfo-check-item';
      const label = problem.fieldElement ? fieldHeading(problem.fieldElement) : 'Kontrola formuláře';
      item.innerHTML = `${escapeHtml(label)}<small>${problem.kind === 'required' ? 'Povinná položka není vyplněna'
        : escapeHtml(problem.message || 'Hodnota nesplňuje podmínku formuláře')}</small>`;
      if (problem.element) item.addEventListener('click', () => { problem.element.scrollIntoView({block: 'center'}); problem.element.focus(); });
      list.append(item);
    }
    queryOne('[data-check]').hidden = false;
    togglePanel('panel', true);
    setStatus(found.length ? `Kontrola: ${found.length} ${found.length === 1 ? 'problém' : found.length < 5 ? 'problémy' : 'problémů'}`
      : scripts.length ? 'Kontrola: bez nálezů, ale není úplná' : 'Kontrola: formulář je vyplněn správně');
    checkWaiter?.(found.length);
    checkWaiter = null;
  }
  const runCheck = () => new Promise(resolve => { checkWaiter = resolve; send(JSON.stringify({type: 'check_form', data: ''})); });
  page.addEventListener('input', event => event.target.classList?.remove('zfo-invalid'), true);
  page.addEventListener('change', event => event.target.classList?.remove('zfo-invalid'), true);

  const helpText = queryOne('[data-help-text]');
  const helpPlaceholder = page.querySelector('[title]:not([title=""])')
    ? 'Klikněte do pole formuláře a zobrazí se jeho nápověda.'
    : 'Tento formulář nápovědu k polím neobsahuje.';
  helpText.innerHTML = `<span class="zfo-muted">${helpPlaceholder}</span>`;
  page.addEventListener('focusin', event => {
    const field = event.target.closest('input, select, textarea');
    if (!field) return;
    const text = field.title.trim();
    helpText.innerHTML = `<b>${escapeHtml(fieldHeading(field))}</b>`
      + (text ? escapeHtml(text) : '<span class="zfo-muted">Pole nemá nápovědu.</span>');
  });

  const print = async (fieldsOnly = false) => {
    await queue;
    document.body.classList.toggle('zfo-print-fields', fieldsOnly === true);
    if (native?.printsNatively) {
      native.postMessage({action: 'print'});
    } else {
      window.print();
      document.body.classList.remove('zfo-print-fields');
    }
  };
  window.zfoPrinted = () => document.body.classList.remove('zfo-print-fields');

  const savedStatus = (verb, path) => {
    const reveal = native ? ` <a data-reveal>${window.zfoIsMac ? 'Zobrazit ve Finderu' : 'Zobrazit ve složce'}</a>` : '';
    setStatusMarkup(`${verb} <b title="${escapeHtml(path)}">${escapeHtml(path.split('/').pop())}</b>${reveal}`);
    queryOne('[data-reveal]')?.addEventListener('click', () => native.postMessage({action: 'reveal', path}));
  };
  const saveXml = (name, folder, xml) => {
    if (native) {
      native.postMessage({action: 'saveFile', name, folder, content: xml});
      return;
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([xml], {type: 'application/xml'}));
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
    setStatusMarkup(`Data stažena jako <b>${escapeHtml(name)}</b>`);
  };
  const exportData = async () => {
    await queue;
    const response = await fetch('/export-data?s=' + SESSION, {method: 'POST'});
    if (!response.ok) { setStatus('Chyba: ' + await response.text()); return; }
    const data = await response.json();
    saveXml(data.name, data.folder, data.xml);
  };
  window.zfoDataSaved = path => savedStatus('Data uložena do', path);
  const importData = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.xml,text/xml,application/xml';
    input.addEventListener('change', async () => {
      const file = input.files[0];
      if (!file) return;
      const response = await fetch('/import-data?s=' + SESSION, {method: 'POST', body: await file.text()});
      if (!response.ok) { setStatus('Import se nepodařil: ' + await response.text()); return; }
      const url = new URL(location.href);
      url.searchParams.delete('blank');
      url.searchParams.set('import', (await response.json()).import);
      location.href = url;
    });
    input.click();
  };

  const splitArgs = text => {
    const parts = [];
    let depth = 0, quote = null, start = 0;
    for (let i = 0; i < text.length; i++) {
      const character = text[i];
      if (quote) { if (character === quote) quote = null; continue; }
      if (character === '"' || character === "'") quote = character;
      else if (character === '(' || character === '{' || character === '[') depth++;
      else if (character === ')' || character === '}' || character === ']') depth--;
      else if (character === ',' && depth === 0) { parts.push(text.slice(start, i).trim()); start = i + 1; }
    }
    parts.push(text.slice(start).trim());
    return parts.filter(Boolean);
  };
  const parseCall = text => {
    const match = /^\s*([\w.]+)\s*\(([\s\S]*)\)\s*$/.exec(text);
    return match && {name: match[1], args: match[2]};
  };
  const engineCommand = async command => {
    gtk_handler_start();
    send(JSON.stringify({type: 'perform_click', data: {command}}));
    await queue;
  };
  async function runExpress(express) {
    if (!ready) return;
    const top = parseCall(express);
    const steps = top?.name === 'LastParam' ? splitArgs(top.args) : [express];
    for (const step of steps) {
      const call = parseCall(step);
      if (call?.name !== 'SendAppEvent') { await engineCommand(step); continue; }
      const [event = '', ...args] = splitArgs(call.args);
      switch (event.replace(/\(\)$/, '').toLowerCase()) {
        case 'checkdata':
          if (await runCheck() && args.includes('stopincomplete')) return;
          break;
        case 'print': await print(args[0] === '2'); break;
        case 'saveform': await actions['save-plain'](); break;
        case 'loaddata': importData(); return;
        case 'recalc': actions.recalc(); await queue; break;
        case 'submit':
          if (/save_data/i.test(args[0] ?? '')) { await exportData(); break; }
          setStatus('Odesílání dat z formuláře není na Macu podporováno.');
          return;
        default: await engineCommand(step);
      }
    }
  }

  const toolbarIcon = selector => queryOne(`.zfo-bar ${selector} svg`).outerHTML;
  const buttonIcons = {
    print: toolbarIcon('[data-action="print"]'),
    saveform: toolbarIcon('[data-menu="save"]'),
    savedata: toolbarIcon('[data-action="export-data"]'),
    loaddata: toolbarIcon('[data-action="import-data"]'),
    printfields: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="1.5"/><path d="M7 14h10v7H7z" stroke-dasharray="2 1.5"/><path d="M9.5 17h5"/></svg>',
  };
  const userButtonBar = queryOne('[data-user-buttons]');
  for (const definition of pageData.buttons) {
    const button = document.createElement('button');
    const fieldsOnly = definition.icon === 'print' && /SendAppEvent\s*\(\s*print\s*,\s*2\s*\)/i.test(definition.express);
    const icon = fieldsOnly ? buttonIcons.printfields
      : buttonIcons[definition.icon] ?? (/loaddata/i.test(definition.express) ? buttonIcons.loaddata : buttonIcons.savedata);
    button.className = 'zfo-btn icon';
    button.title = definition.tooltip || definition.caption;
    button.innerHTML = icon;
    button.addEventListener('click', () => runExpress(definition.express));
    userButtonBar.append(button);
    userButtonBar.hidden = false;
  }
  page.addEventListener('click', async event => {
    const button = event.target.closest('button[zfo-app-event]');
    if (!button?.getAttribute('zfo-app-event').includes('print(')) return;
    event.preventDefault();
    event.stopPropagation();
    if (await runCheck() === 0) print();
  }, true);

  function togglePanel(name, show) {
    const hidden = document.body.classList.toggle(`${name}-hidden`, show === undefined ? undefined : !show);
    queryOne(`.zfo-bar [data-action="${name}"]`).classList.toggle('active', !hidden);
    try { localStorage.setItem(`zfo-${name}-hidden`, hidden ? '1' : '0'); } catch {}
    if (ready) autoZoom();
  }
  for (const name of ['help', 'panel']) {
    let hidden = false;
    try { hidden = localStorage.getItem(`zfo-${name}-hidden`) === '1'; } catch {}
    if (hidden) togglePanel(name, false);
  }

  const closeMenus = () => document.querySelectorAll('[data-menu-list]').forEach(list => { list.hidden = true; });
  for (const button of document.querySelectorAll('[data-menu]')) {
    button.addEventListener('click', event => {
      event.stopPropagation();
      const list = queryOne(`[data-menu-list="${button.dataset.menu}"]`);
      const open = list.hidden;
      closeMenus();
      list.hidden = !open;
    });
  }
  document.addEventListener('click', closeMenus);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenus(); });

  const signDialog = queryOne('[data-sign-dialog]');
  signDialog.addEventListener('close', () => { if (signDialog.returnValue === 'sign') actions.sign(); });

  let lastField = null;
  page.addEventListener('focusin', event => { lastField = event.target.closest('input[name], select[name], textarea[name]') ?? lastField; });
  function resetField() {
    closeMenus();
    if (!lastField) { setStatus('Klikněte do pole, jehož výchozí hodnotu chcete obnovit.'); return; }
    send(JSON.stringify({type: 'default_value', data: {field: lastField.name}}));
  }
  function applyDefault({field, value}) {
    const element = lastField?.name === field ? lastField : document.getElementsByName(field)[0];
    if (!element || value === null) { setStatus('Pole nemá výchozí hodnotu.'); return; }
    if (element.type === 'radio' || element.type === 'checkbox') {
      // The change event of the clicked button would store that button's value, and with no button chosen there is none.
      if (element.type === 'radio') chooseRadio(element, value);
      else checkBoxes(element, value);
      gtk_handler_start();
      send(JSON.stringify({type: 'store_val', data: {field, value, repe: get_parent_repe(element), input_id: element.id}}));
    } else {
      element.value = value;
      $(element).trigger('change');
    }
    const label = shortOneLine(labelFor(element));
    setStatus(label ? `Obnovena výchozí hodnota pole ${label}` : 'Obnovena výchozí hodnota pole');
  }

  const findBar = queryOne('.zfo-find');
  const findInput = document.createElement('input');
  findInput.type = 'search';
  findInput.placeholder = 'Najít ve formuláři';
  let findHits = [], findIndex = -1;
  const highlights = window.CSS?.highlights;
  function clearFind() {
    highlights?.delete('zfo-find');
    highlights?.delete('zfo-find-current');
    page.querySelectorAll('.zfo-find-hit, .zfo-find-current').forEach(element => element.classList.remove('zfo-find-hit', 'zfo-find-current'));
    findHits = []; findIndex = -1;
  }
  function runFind() {
    clearFind();
    const query = findInput.value.trim().toLocaleLowerCase('cs');
    if (query) {
      const walker = document.createTreeWalker(page, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeType === Node.TEXT_NODE) {
          const parent = node.parentElement;
          if (!parent?.offsetParent || parent.closest('script, style, option')) continue;
          const text = node.data.toLocaleLowerCase('cs');
          for (let i = text.indexOf(query); i >= 0; i = text.indexOf(query, i + query.length)) {
            const range = new Range();
            range.setStart(node, i);
            range.setEnd(node, i + query.length);
            findHits.push({range, element: parent});
          }
        } else if (node.matches('input[type=text], textarea, select') && node.offsetParent) {
          const value = node.tagName === 'SELECT' ? node.selectedOptions[0]?.text ?? '' : node.value;
          if (value.toLocaleLowerCase('cs').includes(query)) { node.classList.add('zfo-find-hit'); findHits.push({element: node}); }
        }
      }
      const ranges = findHits.filter(hit => hit.range).map(hit => hit.range);
      if (highlights && ranges.length) highlights.set('zfo-find', new Highlight(...ranges));
    }
    stepFind(1);
  }
  function stepFind(direction) {
    if (findBar.hidden) { openFind(); return; }
    const count = queryOne('[data-find-count]');
    if (!findHits.length) { count.textContent = findInput.value.trim() ? 'Nenalezeno' : ''; return; }
    findHits[findIndex]?.element.classList.remove('zfo-find-current');
    findIndex = (findIndex + direction + findHits.length) % findHits.length;
    const hit = findHits[findIndex];
    if (hit.range) highlights?.set('zfo-find-current', new Highlight(hit.range));
    else { highlights?.delete('zfo-find-current'); hit.element.classList.add('zfo-find-current'); }
    hit.element.scrollIntoView({block: 'center'});
    count.textContent = `${findIndex + 1} z ${findHits.length}`;
  }
  function openFind() {
    closeMenus();
    findBar.hidden = false;
    findInput.focus();
    findInput.select();
  }
  function closeFind() { findBar.hidden = true; clearFind(); queryOne('[data-find-count]').textContent = ''; }
  let findTimer;
  findInput.addEventListener('input', () => { clearTimeout(findTimer); findTimer = setTimeout(runFind, 150); });
  findInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); stepFind(event.shiftKey ? -1 : 1); }
    if (event.key === 'Escape') { event.preventDefault(); closeFind(); }
  });
  findBar.querySelectorAll('[data-find-step]').forEach(button => button.addEventListener('click', () => stepFind(Number(button.dataset.findStep))));
  queryOne('[data-find-close]').addEventListener('click', closeFind);

  let zoom = 1;
  let autoFit = true;
  const applyZoom = wanted => { zoom = Math.min(3, Math.max(.4, wanted)); page.style.zoom = zoom; queryOne('.zfo-zoom').textContent = Math.round(zoom * 100) + ' %'; };
  const widthZoom = () => (queryOne('.zfo-stage').clientWidth - 48) / (page.getBoundingClientRect().width / zoom);
  const fitWidth = () => applyZoom(widthZoom());
  const autoZoom = () => { if (autoFit) applyZoom(Math.min(1, widthZoom())); };
  const manualZoom = wanted => { autoFit = false; applyZoom(wanted); };
  window.addEventListener('resize', autoZoom);

  const actions = {
    open: () => native.postMessage({action: 'open'}),
    support: () => zfoSupport(),
    'zoom-in': () => manualZoom(zoom + .1),
    'zoom-out': () => manualZoom(zoom - .1),
    'zoom-fit': () => { autoFit = false; fitWidth(); },
    panel: () => togglePanel('panel'),
    help: () => togglePanel('help'),
    print,
    check: runCheck,
    'import-data': importData,
    'export-data': exportData,
    recalc: () => { gtk_handler_start(); send(JSON.stringify({type: 'recalc_form', data: ''})); setStatus('Hodnoty přepočítány'); },
    find: () => openFind(),
    'find-next': () => stepFind(1),
    'find-previous': () => stepFind(-1),
    'reset-field': resetField,
    reset: () => {
      if (!confirm('Obnovit výchozí hodnoty ve všech polích formuláře? Neuložené změny se ztratí.')) return;
      const url = new URL(location.href);
      url.searchParams.set('blank', '1');
      location.href = url;
    },
    'save-plain': async () => {
      await queue;
      if (signatures.some(signature => !signature.publisher)
          && !confirm('Uložením bez podpisu se z formuláře odstraní stávající elektronický podpis. Pokračovat?')) return;
      const response = await fetch('/save?s=' + SESSION, {method: 'POST'});
      if (!response.ok) { setStatus('Chyba: ' + await response.text()); return; }
      const result = await response.json();
      signatures = result.signatures; changed = false; renderSignatures();
      savedStatus('Uloženo do', result.saved);
    },
    save: () => { closeMenus(); signDialog.returnValue = ''; signDialog.showModal(); certSelect.focus(); },
    sign: async () => {
      await queue;
      if (!certSelect.value) { setStatus('Vyber certifikát pro podpis.'); actions.save(); return; }
      certificates.remember();
      setStatus('Podepisuji… (token si může vyžádat PIN)');
      const response = await fetch('/save?s=' + SESSION + '&cert=' + encodeURIComponent(certSelect.value),
                              {method: 'POST', body: JSON.stringify({password: certificates.password()})});
      if (!response.ok) { setStatus('Chyba: ' + await response.text()); return; }
      const result = await response.json();
      signatures = result.signatures; changed = false; renderSignatures();
      savedStatus('Podepsáno a uloženo jako', result.saved);
    },
  };
  // A hidden page has no visible fields: until it is shown, a check would find nothing wrong and the zoom nothing to fit.
  const AVAILABLE_WHILE_OPENING = new Set(['open', 'support', 'help', 'panel']);
  const runAction = name => { if (ready || AVAILABLE_WHILE_OPENING.has(name)) actions[name]?.(); };
  for (const button of document.querySelectorAll('.zfo-bar [data-action], .zfo-close[data-action]')) {
    button.addEventListener('click', () => runAction(button.dataset.action));
  }
  window.zfoAction = runAction;
  document.addEventListener('keydown', event => {
    const shortcut = event.key === 'F9' ? (event.shiftKey ? 'check' : 'recalc')
      : !window.zfoShortcut(event) ? null
      : event.key === '+' || event.key === '=' ? 'zoom-in'
      : event.key === '-' ? 'zoom-out'
      : event.key.toLowerCase() === 's' ? (event.shiftKey ? 'save' : 'save-plain')
      : event.key === 'p' ? 'print'
      : event.key === 'f' ? 'find'
      : event.key.toLowerCase() === 'g' ? (event.shiftKey ? 'find-previous' : 'find-next')
      : event.key === 'Backspace' && event.shiftKey ? 'reset-field' : null;
    if (!shortcut) return;
    event.preventDefault();
    runAction(shortcut);
  });
})();
