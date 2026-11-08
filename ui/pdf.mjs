const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const SESSION = pageData.session;
const native = window.zfoNative;
const queryOne = selector => document.querySelector(selector);
const status = queryOne('[data-status]');
const setStatus = text => { status.textContent = text; };
const setStatusMarkup = markup => { status.innerHTML = markup; };
const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
queryOne('[data-file]').textContent = pageData.file;
if (native) queryOne('[data-native]').hidden = false;

// pdf_viewer.mjs picks up the library from the global it expects.
const pdfjsLib = await import('/pdfjs/build/pdf.min.mjs');
globalThis.pdfjsLib = pdfjsLib;
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.min.mjs';
const {PDFViewer, EventBus, PDFLinkService} = await import('/pdfjs/web/pdf_viewer.mjs');

const eventBus = new EventBus();
const linkService = new PDFLinkService({eventBus});
const viewer = new PDFViewer({
  container: queryOne('#viewerContainer'), viewer: queryOne('#viewer'), eventBus, linkService,
  annotationMode: pdfjsLib.AnnotationMode.ENABLE_FORMS, textLayerMode: 1,
});
linkService.setViewer(viewer);
let pdf, fieldObjects = {}, changed = false, autoFit = true;

const storeFieldValue = (widget, value) => {
  if (widget.type === 'radiobutton') pdf.annotationStorage.setValue(widget.id, {value: [widget.buttonValue, widget.exportValues].includes(value)});
  else if (widget.type === 'checkbox') pdf.annotationStorage.setValue(widget.id, {value: Boolean(value)});
  else if (['combobox', 'listbox'].includes(widget.type)) pdf.annotationStorage.setValue(widget.id, {value: [String(value)]});
  // A text field shows the text of its saved appearance unless a formatted value is given; an empty one would not
  // count, so a space stands in for it on screen while the value itself stays empty.
  else if (widget.type === 'text') pdf.annotationStorage.setValue(widget.id, {value: String(value), formattedValue: String(value) || ' '});
};
// Values go into the storage before the viewer draws the fields: the drawn fields read their value from there once.
const loadDocument = async (valuesToShow = {}) => {
  pdf = await pdfjsLib.getDocument({
    url: '/pdf?s=' + SESSION + '&t=' + Date.now(), standardFontDataUrl: '/pdfjs/standard_fonts/',
    cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
  }).promise;
  fieldObjects = (await pdf.getFieldObjects()) ?? {};
  for (const [name, value] of Object.entries(valuesToShow)) {
    for (const widget of fieldObjects[name] ?? []) if (widget.id) storeFieldValue(widget, value);
  }
  changed = Object.keys(valuesToShow).length > 0;
  pdf.annotationStorage.onSetModified = () => { changed = true; renderSignatures(); };
  const pagesShown = new Promise(resolve => eventBus.on('pagesinit', resolve, {once: true}));
  viewer.setDocument(pdf);
  linkService.setDocument(pdf);
  renderSignatures();
  await pagesShown;
};
eventBus.on('pagesinit', () => { if (autoFit) viewer.currentScaleValue = 'page-width'; setStatus('Připraveno'); });
eventBus.on('scalechanging', event => { queryOne('.zfo-zoom').textContent = Math.round(event.scale * 100) + ' %'; });
new ResizeObserver(() => { if (autoFit && pdf) viewer.currentScaleValue = 'page-width'; }).observe(queryOne('#viewerContainer'));

const changedFieldValues = () => {
  const result = {};
  for (const [name, widgets] of Object.entries(fieldObjects)) {
    for (const widget of widgets) {
      const stored = widget.id && pdf.annotationStorage.getRawValue(widget.id);
      if (!stored || !('value' in stored)) continue;
      if (widget.type === 'radiobutton') {
        if (stored.value) result[name] = widget.exportValues ?? widget.buttonValue;
      } else if (widget.type === 'checkbox') {
        result[name] = Boolean(stored.value);
      } else if (['combobox', 'listbox'].includes(widget.type)) {
        result[name] = Array.isArray(stored.value) ? stored.value[0] ?? '' : stored.value;
      } else if (widget.type === 'text') {
        result[name] = stored.value;
      }
    }
  }
  return result;
};

const info = pageData.info;
queryOne('[data-info]').innerHTML = [
  info.title && `<b>${escapeHtml(info.title)}</b>`,
  info.author && `Autor: ${escapeHtml(info.author)}`,
  `Stran: ${escapeHtml(info.pages)}`,
  info.fields ? `Vyplnitelných polí: ${escapeHtml(info.fields)}` : 'Dokument nemá vyplnitelná pole.',
  info.xfa && 'Dokument obsahuje formulář XFA, který Blanketa nepodporuje.',
].filter(Boolean).join('<br>');
if (info.xfa) {
  const notice = queryOne('[data-notice]');
  notice.querySelector('span').textContent = info.xfa === 'dynamic'
    ? 'Toto je dynamický formulář XFA (Adobe LiveCycle), který Blanketa neumí zobrazit ani vyplnit. Otevřete ho '
      + 'v Adobe Acrobat Readeru, nebo si u úřadu vyžádejte jinou verzi formuláře.'
    : 'Tento PDF formulář obsahuje i verzi XFA, kterou Blanketa nepodporuje. Vyplní jen běžná pole, takže programy '
      + 'pracující s XFA (například Adobe Acrobat Reader) mohou ukázat jiné hodnoty. Před odesláním soubor zkontrolujte.';
  notice.querySelector('button').addEventListener('click', () => { notice.hidden = true; });
  notice.hidden = false;
}

const icons = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="#1e8e3e" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="#b26a00" stroke-width="2"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/></svg>',
  bad: '<svg viewBox="0 0 24 24" fill="none" stroke="#c5221f" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
};
const formatDateTime = iso => iso ? new Date(iso).toLocaleString('cs-CZ', {dateStyle: 'medium', timeStyle: 'short'}) : '–';
const stampLine = signature => signature.proof
  ? `<div class="meta">Čas potvrzuje ${signature.proof.kind === 'signature' ? 'kvalifikované časové razítko' : 'kvalifikované časové razítko dokumentu'}: ${formatDateTime(signature.proof.at)}, vydal ${escapeHtml(signature.proof.issuer)}.</div>`
  : signature.stamp_problem ? `<div class="meta">Časové razítko čas nepotvrzuje. ${escapeHtml(signature.stamp_problem)}</div>` : '';
const stampCard = signature => {
  const level = !signature.intact ? 'bad' : signature.proof ? 'ok' : 'warn';
  const state = !signature.intact ? 'Časové razítko dokumentu je poškozené'
    : signature.proof ? 'Časové razítko dokumentu je platné'
    : signature.stamp_unverifiable ? 'Časové razítko dokumentu je neporušené, důvěryhodnost nešlo ověřit'
    : 'Časové razítko dokumentu je neporušené, ale nedůvěryhodné';
  return `<div class="zfo-card ${level}">
    <div class="who">${icons[level]}${escapeHtml(signature.subject)}</div>
    <div class="state">${state}</div>
    ${stampLine(signature)}
    ${signature.intact && !signature.covers_whole ? '<div class="meta">Po razítku byl dokument doplněn nebo změněn, razítko se vztahuje jen k dřívější verzi.</div>' : ''}
    <div class="meta">Vystavil: ${escapeHtml(signature.issuer)}</div>
  </div>`;
};
const signatureCard = signature => {
  if (signature.document_stamp) return stampCard(signature);
  const expired = new Date(signature.not_after) < new Date(signature.proof?.at ?? Date.now());
  const revocation = window.zfoRevocation;
  const level = !signature.intact || revocation.revokedBeforeSigning(signature) ? 'bad'
    : (signature.trusted && !expired && !signature.modified && !signature.changes_unchecked && !revocation.revoked(signature)) ? 'ok' : 'warn';
  const state = !signature.intact ? 'Podpis je porušen – podepsaný obsah byl změněn'
    : revocation.state(signature) ? revocation.state(signature)
    : signature.modified ? 'Podepsaná verze je neporušená, ale po podpisu se změnilo víc než vyplněná pole'
    : signature.changes_unchecked ? 'Podepsaná verze je neporušená, ale změny po podpisu se nepodařilo zkontrolovat'
    : signature.trusted ? 'Podpis je platný, certifikát je důvěryhodný'
    : signature.unverifiable ? 'Obsah je neporušený, důvěryhodnost certifikátu se nepodařilo ověřit'
    : 'Obsah je neporušený, certifikát není hodnocen jako důvěryhodný';
  return `<div class="zfo-card ${level}" title="${escapeHtml(signature.reason || '')}">
    <div class="who">${icons[level]}${escapeHtml(signature.subject)}</div>
    <div class="state">${state}</div>
    ${signature.trust_source === 'eu' ? '<div class="meta">Vydavatel je v důvěryhodném seznamu EU.</div>' : ''}
    ${signature.unverifiable ? `<div class="meta">${escapeHtml(signature.reason)}</div>` : ''}
    ${revocation.line(signature)}
    ${stampLine(signature)}
    <div class="meta">Podepsáno: ${formatDateTime(signature.signed_at)}${signature.proof ? ' (uvádí podepisující)' : ''}<br>Vystavil: ${escapeHtml(signature.issuer)}<br>
      ${new Date(signature.not_after) < new Date() ? 'Platnost certifikátu skončila' : 'Certifikát platí do'} ${new Date(signature.not_after).toLocaleDateString('cs-CZ')}
      ${signature.intact && !signature.covers_whole && !signature.changes_unchecked ? '<br>Po podpisu byla doplněna pole nebo další podpisy.' : ''}</div>
  </div>`;
};
let signatures = pageData.signatures;
const renderSignatures = () => {
  queryOne('[data-signatures]').innerHTML =
    (changed && signatures.length ? '<div class="zfo-note">Vyplněné hodnoty se uloží jako dodatek; stávající podpisy zůstanou platné, pokud je dokument nezamyká.</div>' : '') +
    (signatures.length ? signatures.map(signatureCard).join('') : '<div class="zfo-empty">Dokument zatím není podepsaný.</div>');
  window.zfoRevocation.ask(SESSION, signatures.filter(signature => !signature.document_stamp), renderSignatures);
};

const certificates = window.zfoCertificates(SESSION);
const certSelect = certificates.select;
queryOne('[data-cert-slot]').replaceWith(certificates.element);

const savedStatus = (verb, path) => {
  const reveal = native ? ` <a data-reveal>${window.zfoIsMac ? 'Zobrazit ve Finderu' : 'Zobrazit ve složce'}</a>` : '';
  setStatusMarkup(`${verb} <b title="${escapeHtml(path)}">${escapeHtml(path.split('/').pop())}</b>${reveal}`);
  queryOne('[data-reveal]')?.addEventListener('click', () => native.postMessage({action: 'reveal', path}));
};
const postChangedValues = async (path, extra = '', more = {}) => {
  document.activeElement?.blur();
  const response = await fetch(`${path}?s=${SESSION}${extra}`, {method: 'POST', body: JSON.stringify({values: changedFieldValues(), ...more})});
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};
const showSavedDocument = async (result, verb) => {
  signatures = result.signatures;
  signatureField = result.signature_field;
  queryOne('[data-file]').textContent = result.saved.split('/').pop();
  await loadDocument();
  savedStatus(verb, result.saved);
};

let printFrame;
const printPdfInFrame = async () => {
  document.activeElement?.blur();
  const response = await fetch(`/pdf-print-document?s=${SESSION}`, {method: 'POST', body: JSON.stringify({values: changedFieldValues()})});
  if (!response.ok) throw new Error(await response.text());
  if (printFrame) {
    URL.revokeObjectURL(printFrame.src);
    printFrame.remove();
  }
  printFrame = document.createElement('iframe');
  printFrame.className = 'zfo-print-frame';
  printFrame.addEventListener('load', event => event.target.contentWindow.print(), {once: true});
  printFrame.src = URL.createObjectURL(await response.blob());
  document.body.append(printFrame);
};

window.zfoDataSaved = path => savedStatus('Data uložena do', path);
const exportData = async () => {
  try {
    const data = await postChangedValues('/pdf-export-data');
    if (native) {
      native.postMessage({action: 'saveFile', name: data.name, folder: data.folder, content: data.contents.xfdf, contents: data.contents});
      return;
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([data.contents.xfdf], {type: 'application/vnd.adobe.xfdf'}));
    link.download = data.name;
    link.click();
    URL.revokeObjectURL(link.href);
    setStatusMarkup(`Data stažena jako <b>${escapeHtml(data.name)}</b>`);
  } catch (error) { setStatus('Chyba: ' + error.message); }
};
const importReport = queryOne('[data-import-report]');
const listInReport = (selector, names) => {
  const section = importReport.querySelector(selector);
  section.hidden = !names.length;
  section.querySelector('ul').replaceChildren(...names.map(name => Object.assign(document.createElement('li'), {textContent: name})));
};
const loadedValues = count => count === 0 ? 'se nenačetla žádná hodnota'
  : count === 1 ? 'se načetla 1 hodnota' : count <= 4 ? `se načetly ${count} hodnoty` : `se načetlo ${count} hodnot`;
const showImportResult = (fileName, result) => {
  const loaded = Object.keys(result.values).length;
  const summary = `Ze souboru „${fileName}“ ${loadedValues(loaded)}.` + (loaded ? ` Zkontrolujte ${loaded === 1 ? 'ji' : 'je'} a dokument uložte.` : '');
  setStatus(summary);
  if (!result.missing.length && !result.unchangeable.length && !result.not_offered.length && !result.several_values.length) return;
  importReport.querySelector('[data-import-summary]').textContent = summary;
  listInReport('[data-import-missing]', result.missing);
  listInReport('[data-import-unchangeable]', result.unchangeable);
  listInReport('[data-import-not-offered]', result.not_offered.map(({field, value}) => `${field}: „${value}“`));
  listInReport('[data-import-several-values]', result.several_values);
  importReport.showModal();
};
const importData = () => {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.xfdf,.fdf,application/vnd.adobe.xfdf,application/vnd.fdf';
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    document.activeElement?.blur();
    try {
      const response = await fetch('/pdf-import-data?s=' + SESSION, {method: 'POST', body: await file.arrayBuffer()});
      if (!response.ok) { setStatus('Import se nepodařil: ' + await response.text()); return; }
      const result = await response.json();
      await loadDocument({...changedFieldValues(), ...result.values});
      showImportResult(file.name, result);
    } catch (error) { setStatus('Import se nepodařil: ' + error.message); }
  });
  input.click();
};

let signatureField = info.signature_field;
const signMode = () => queryOne('[data-sign-mode] input:checked').value;
try { const mode = localStorage.getItem('zfo-sign-mode'); if (mode) queryOne(`[data-sign-mode] input[value="${mode}"]`).checked = true; } catch {}
const signWith = async placement => {
  setStatus('Podepisuji… (token si může vyžádat PIN)');
  try {
    await showSavedDocument(await postChangedValues('/pdf-save', '&cert=' + encodeURIComponent(certSelect.value),
                           {placement, password: certificates.password()}), 'Podepsáno a uloženo jako');
  } catch (error) { setStatus('Chyba: ' + error.message); }
};
const MIN_DRAGGED_BOX_PX = 12, SIGNATURE_WIDTH_PT = 180, SIGNATURE_HEIGHT_PT = 60;
let placing = null;
const stopPlacing = () => {
  placing?.box?.remove();
  placing = null;
  document.body.classList.remove('zfo-placing');
};
const startPlacing = () => {
  document.body.classList.add('zfo-placing');
  placing = {};
  setStatus('Tažením myši vyznačte na stránce místo pro podpis, klepnutím vložíte podpis běžné velikosti. Esc zruší.');
};
const pointIn = (page, event) => {
  const rect = page.getBoundingClientRect();
  return [Math.min(Math.max(event.clientX - rect.left, 0), rect.width), Math.min(Math.max(event.clientY - rect.top, 0), rect.height)];
};
queryOne('#viewer').addEventListener('pointerdown', event => {
  const page = placing && event.target.closest('.page');
  if (!page) return;
  event.preventDefault();
  const box = document.createElement('div');
  box.className = 'zfo-place-box';
  page.append(box);
  Object.assign(placing, {page, box, start: pointIn(page, event)});
  try { page.setPointerCapture(event.pointerId); } catch {}
});
queryOne('#viewer').addEventListener('pointermove', event => {
  if (!placing?.box) return;
  const [x, y] = pointIn(placing.page, event), [startX, startY] = placing.start;
  Object.assign(placing.box.style, {left: Math.min(x, startX) + 'px', top: Math.min(y, startY) + 'px',
                                    width: Math.abs(x - startX) + 'px', height: Math.abs(y - startY) + 'px'});
});
queryOne('#viewer').addEventListener('pointerup', event => {
  if (!placing?.box) return;
  const index = Number(placing.page.dataset.pageNumber) - 1;
  const {viewport} = viewer.getPageView(index);
  const end = pointIn(placing.page, event), start = placing.start;
  stopPlacing();
  let corners;
  const isClick = Math.abs(end[0] - start[0]) < MIN_DRAGGED_BOX_PX || Math.abs(end[1] - start[1]) < MIN_DRAGGED_BOX_PX;
  if (isClick) {
    const [x, y] = viewport.convertToPdfPoint(...end);
    corners = [[x - SIGNATURE_WIDTH_PT / 2, y - SIGNATURE_HEIGHT_PT / 2], [x + SIGNATURE_WIDTH_PT / 2, y + SIGNATURE_HEIGHT_PT / 2]];
  } else {
    corners = [viewport.convertToPdfPoint(...start), viewport.convertToPdfPoint(...end)];
  }
  const xs = corners.map(corner => corner[0]), ys = corners.map(corner => corner[1]);
  signWith({page: index, box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]});
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && placing) { stopPlacing(); setStatus('Podepisování zrušeno'); }
});

const signDialog = queryOne('[data-sign-dialog]');
signDialog.addEventListener('close', () => { if (signDialog.returnValue === 'sign') actions.sign(); });
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

const zoomTo = scale => { autoFit = false; viewer.currentScale = Math.min(4, Math.max(.25, scale)); };
const actions = {
  open: () => native?.postMessage({action: 'open'}),
  support: () => zfoSupport(),
  'save-plain': async () => {
    closeMenus();
    try { await showSavedDocument(await postChangedValues('/pdf-save'), 'Uloženo do'); } catch (error) { setStatus('Chyba: ' + error.message); }
  },
  save: () => {
    closeMenus();
    queryOne('[data-sign-text]').textContent = 'Dokument se podepíše vybraným certifikátem a uloží jako nový soubor vedle původního. '
      + (signatureField ? `Podpis se vloží do připraveného podpisového pole „${signatureField}“.`
                        : 'Dokument nemá připravené podpisové pole.');
    queryOne('[data-sign-mode]').hidden = Boolean(signatureField);
    signDialog.returnValue = '';
    signDialog.showModal();
    certSelect.focus();
  },
  sign: async () => {
    if (!certSelect.value) { setStatus('Vyber certifikát pro podpis.'); actions.save(); return; }
    try { localStorage.setItem('zfo-cert', certSelect.value); localStorage.setItem('zfo-sign-mode', signMode()); } catch {}
    if (!signatureField && signMode() === 'place') startPlacing();
    else signWith(null);
  },
  print: async () => {
    try {
      if (native?.printsPdfInPage) {
        await printPdfInFrame();
        return;
      }
      const {path} = await postChangedValues('/pdf-print');
      if (native) native.postMessage({action: 'printPdf', path});
      else window.print();
    } catch (error) { setStatus('Chyba: ' + error.message); }
  },
  reset: async () => {
    if (changed && !confirm('Zahodit neuložené změny a načíst hodnoty z uloženého souboru?')) return;
    await loadDocument();
    setStatus('Načteny hodnoty z uloženého souboru');
  },
  'import-data': importData,
  'export-data': exportData,
  'zoom-in': () => zoomTo(viewer.currentScale + .1),
  'zoom-out': () => zoomTo(viewer.currentScale - .1),
  'zoom-fit': () => { autoFit = true; viewer.currentScaleValue = 'page-width'; },
  panel: () => {
    const hidden = document.body.classList.toggle('panel-hidden');
    queryOne('.zfo-bar [data-action="panel"]').classList.toggle('active', !hidden);
  },
};
for (const button of document.querySelectorAll('.zfo-bar [data-action], .zfo-close[data-action]')) {
  button.addEventListener('click', () => actions[button.dataset.action]());
}
window.zfoAction = name => actions[name]?.();
document.addEventListener('keydown', event => {
  if (!window.zfoShortcut(event)) return;
  const shortcut = event.key === '+' || event.key === '=' ? 'zoom-in' : event.key === '-' ? 'zoom-out'
    : event.key.toLowerCase() === 's' ? (event.shiftKey ? 'save' : 'save-plain') : event.key === 'p' ? 'print' : null;
  if (!shortcut || native?.platform === 'mac') return;
  event.preventDefault();
  actions[shortcut]();
});

await loadDocument();
