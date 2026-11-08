const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const SESSION = pageData.session;
const MESSAGE = pageData.message;
const SUBMITTED = pageData.submitted;
let signatures = pageData.signatures;
const native = window.zfoNative;
const PORTAL = 'https://www.mojedatovaschranka.cz/';
const KINDS = {received: 'Přijatá datová zpráva', sent: 'Odeslaná datová zpráva', receipt: 'Doručenka'};
const STATES = {1: 'Podána', 2: 'Opatřena časovým razítkem', 3: 'Neprošla antivirovou kontrolou', 4: 'Dodána',
                5: 'Doručena fikcí', 6: 'Doručena přihlášením', 7: 'Přečtena', 8: 'Nedoručitelná',
                10: 'Přesunuta do Datového trezoru'};
const FOREIGN_SEAL = 'Zprávu nezapečetil Informační systém datových schránek';
const queryOne = selector => document.querySelector(selector);
const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
const formatDateTime = iso => iso ? new Date(iso).toLocaleString('cs-CZ', {dateStyle: 'medium', timeStyle: 'short'}) : '–';
const status = queryOne('[data-status]');
const element = (tag, text, className) => {
  const created = document.createElement(tag);
  if (text !== undefined) created.textContent = text;
  if (className) created.className = className;
  return created;
};
const showNotice = (text, className) => {
  const notice = queryOne('[data-notice]');
  notice.querySelector('span').textContent = text;
  if (className) notice.classList.add(className);
  notice.querySelector('button').addEventListener('click', () => { notice.hidden = true; });
  notice.hidden = false;
};

queryOne('[data-file]').textContent = pageData.file;
document.title = MESSAGE.subject || KINDS[MESSAGE.kind];
if (native) queryOne('[data-native]').hidden = false;
queryOne('[data-subject]').textContent = MESSAGE.subject || 'Bez věci';
queryOne('[data-kind]').textContent = `${KINDS[MESSAGE.kind]}${MESSAGE.id ? ` · ID zprávy ${MESSAGE.id}` : ''}`;
const party = ({name, address, box}) => [name, address, box && `ID schránky ${box}`].filter(Boolean).join('\n');
const fields = [
  ['Odesílatel', party(MESSAGE.sender)], ['Příjemce', party(MESSAGE.recipient)], ['K rukám', MESSAGE.to_hands],
  ['Č. j. odesílatele', MESSAGE.sender_ref], ['Spis. zn. odesílatele', MESSAGE.sender_ident],
  ['Č. j. příjemce', MESSAGE.recipient_ref], ['Spis. zn. příjemce', MESSAGE.recipient_ident],
  ['Dodáno', MESSAGE.delivered && formatDateTime(MESSAGE.delivered)],
  ['Doručeno', MESSAGE.accepted && formatDateTime(MESSAGE.accepted)],
  ['Stav', MESSAGE.status === null ? '' : STATES[MESSAGE.status] ?? `Stav ${MESSAGE.status}`],
  ['Do vlastních rukou', MESSAGE.personal_delivery ? 'Ano' : ''],
];
for (const [label, value] of fields.filter(([, value]) => value)) {
  queryOne('[data-fields]').append(element('dt', label), element('dd', value));
}
if (MESSAGE.suspicious) showNotice('ISDS zprávu označil jako podezřelou.');

let fileView = null;
if (MESSAGE.kind !== 'receipt') {
  const attachments = MESSAGE.attachments;
  const isPdf = attachment => String(attachment.mime).toLowerCase() === 'application/pdf' || /\.pdf$/i.test(attachment.name);
  const initial = [attachments.findIndex(attachment => attachment.main), attachments.findIndex(isPdf), 0].find(index => index >= 0);
  queryOne('[data-attachments]').hidden = false;
  queryOne('[data-attachments-title]').textContent = `Přílohy (${attachments.length})`;
  fileView = window.zfoFiles({
    files: attachments.map((attachment, index) => ({...attachment, url: `/attachment?s=${SESSION}&i=${index}`, pdf: isPdf(attachment)})),
    list: queryOne('[data-attachment-list]'), stage: queryOne('.zfo-stage'), toolbar: queryOne('[data-file-tools]'), status, initial,
    emptyText: 'Zpráva nemá žádné přílohy.', failedText: 'Přílohu se nepodařilo uložit',
  });
} else {
  queryOne('[data-file-tools]').remove();
  queryOne('[data-events]').hidden = !MESSAGE.events.length;
  for (const event of MESSAGE.events) {
    const item = element('li');
    const time = element('time', formatDateTime(event.time));
    item.append(time, document.createTextNode(event.description));
    queryOne('[data-event-list]').append(item);
  }
}

const submitted = queryOne('[data-submitted]');
submitted.textContent = SUBMITTED?.matches
  ? `Podáno ${formatDateTime(SUBMITTED.at)} podle časového razítka v\u00a0pečetěném obsahu zprávy, vydal ${SUBMITTED.issuer}.`
  : 'Zpráva neobsahuje časové razítko podání, které by k ní patřilo.';

const revocation = window.zfoRevocation;
const icons = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="#1e8e3e" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="#b26a00" stroke-width="2"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/></svg>',
  bad: '<svg viewBox="0 0 24 24" fill="none" stroke="#c5221f" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
};
const stampLine = seal => seal.proof
  ? `<div class="meta">Čas zapečetění potvrzuje kvalifikované časové razítko: ${formatDateTime(seal.proof.at)}, vydal ${escapeHtml(seal.proof.issuer)}.</div>`
  : seal.stamp_problem ? `<div class="meta">Časové razítko čas nepotvrzuje. ${escapeHtml(seal.stamp_problem)}</div>` : '';
const sealState = seal => {
  const expired = new Date(seal.not_after) < new Date(seal.proof?.at ?? Date.now());
  if (!seal.intact) return ['bad', 'Pečeť je porušena – obsah zprávy byl po zapečetění změněn'];
  if (revocation.revokedBeforeSigning(seal)) return ['bad', revocation.state(seal)];
  if (!seal.isds_operator) return ['warn', `${FOREIGN_SEAL}. Pečeť je neporušená${seal.trusted ? ', certifikát je důvěryhodný' : ''}.`];
  if (revocation.revoked(seal)) return ['warn', revocation.state(seal)];
  if (!seal.trusted) return ['warn', seal.unverifiable ? 'Obsah je neporušený, důvěryhodnost certifikátu se nepodařilo ověřit'
                                                       : 'Obsah je neporušený, ale certifikát pečeti není důvěryhodný'];
  if (expired) return ['warn', 'Pečeť je neporušená, certifikát už vypršel a čas zapečetění nepotvrzuje časové razítko.', true];
  return ['ok', 'Pečeť ISDS je platná, certifikát je důvěryhodný'];
};
const sealCard = seal => {
  const [level, state, offerPortal] = sealState(seal);
  return `<div class="zfo-card ${level}" title="${escapeHtml(seal.reason || '')}">
    <div class="who">${icons[level]}${escapeHtml(seal.isds_operator ? 'Informační systém datových schránek' : seal.subject)}</div>
    <div class="state">${escapeHtml(state)}</div>
    ${seal.trust_source === 'eu' ? '<div class="meta">Vydavatel je v důvěryhodném seznamu EU.</div>' : ''}
    ${seal.unverifiable ? `<div class="meta">${escapeHtml(seal.reason)}</div>` : ''}
    ${revocation.line(seal)}
    ${stampLine(seal)}
    <div class="meta">Zapečetil: ${escapeHtml(seal.subject)}<br>Vystavil: ${escapeHtml(seal.issuer)}<br>
      ${new Date(seal.not_after) < new Date() ? 'Platnost certifikátu skončila' : 'Certifikát platí do'} ${new Date(seal.not_after).toLocaleDateString('cs-CZ')}</div>
    ${offerPortal ? '<div class="meta">Pravost ověříte nahráním souboru na portálu datových schránek. <button type="button" class="zfo-link" data-portal>Otevřít portál</button></div>' : ''}
  </div>`;
};
const renderSeal = () => {
  queryOne('[data-signatures]').innerHTML = signatures.map(sealCard).join('');
  queryOne('[data-portal]')?.addEventListener('click', () => window.zfoOpenUrl(PORTAL));
  revocation.ask(SESSION, signatures, renderSeal);
};
if (signatures.some(seal => !seal.isds_operator)) {
  showNotice(`${FOREIGN_SEAL}. Zapečetil ji: ${signatures.find(seal => !seal.isds_operator).subject}.`, 'zfo-foreign-seal');
}
renderSeal();

const actions = {
  ...fileView?.actions,
  open: () => native?.postMessage({action: 'open'}),
  support: () => zfoSupport(),
  panel: () => {
    const hidden = document.body.classList.toggle('panel-hidden');
    queryOne('.zfo-bar [data-action="panel"]').classList.toggle('active', !hidden);
  },
};
for (const button of document.querySelectorAll('.zfo-bar [data-action], .zfo-close[data-action]')) {
  button.addEventListener('click', () => actions[button.dataset.action]());
}
const explainReadOnly = () => { status.textContent = 'Datová zpráva je jen ke čtení – nejde ji uložit, podepsat ani upravit.'; };
window.zfoAction = name => (actions[name] ?? explainReadOnly)();
