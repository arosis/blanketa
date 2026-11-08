const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const SESSION = pageData.session;
const FILE = pageData.file;
const CONTAINER = pageData.container;
const queryOne = selector => document.querySelector(selector);
const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
const STATE_LABELS = {changed: 'změněno po podpisu', signed: 'podepsáno', invalid: 'podpis neplatný', unverified: 'neověřeno', unsigned: 'nepodepsáno'};
const files = CONTAINER.files;
const plural = (count, one, few, many) => count === 1 ? one : count >= 2 && count <= 4 ? few : many;
document.title = FILE;
queryOne('[data-file]').textContent = FILE;

const unsigned = files.filter(file => file.state === 'unsigned').length;
const info = queryOne('[data-info]');
info.append(Object.assign(document.createElement('b'), {textContent: CONTAINER.label}), document.createElement('br'),
            `Soubory: ${files.length}` + (unsigned ? `, z\u00a0toho ${unsigned} ${plural(unsigned, 'nepodepsaný', 'nepodepsané', 'nepodepsaných')}` : ''));
queryOne('[data-warnings]').append(...[...CONTAINER.warnings, CONTAINER.unsupported].filter(Boolean)
  .map(text => Object.assign(document.createElement('div'), {className: 'zfo-note', textContent: text})));

const fileView = window.zfoFiles({
  files: files.map((file, index) => ({...file, url: `/container-file?s=${SESSION}&i=${index}`, stateLabel: STATE_LABELS[file.state] ?? file.state})),
  list: queryOne('[data-files]'), stage: queryOne('.zfo-stage'), toolbar: queryOne('[data-file-tools]'), status: queryOne('[data-status]'),
  initial: Math.max(0, files.findIndex(file => file.pdf)),
  emptyText: 'Kontejner neobsahuje žádné soubory.', failedText: 'Soubor se nepodařilo uložit',
});

const icons = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="#1e8e3e" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="#b26a00" stroke-width="2"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/></svg>',
  bad: '<svg viewBox="0 0 24 24" fill="none" stroke="#c5221f" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>',
};
const formatDateTime = iso => iso ? new Date(iso).toLocaleString('cs-CZ', {dateStyle: 'medium', timeStyle: 'short'}) : '–';
const stampLine = signature => signature.proof
  ? `<div class="meta">Čas potvrzuje ${signature.proof.kind === 'signature' ? 'kvalifikované časové razítko' : 'kvalifikované časové razítko dokumentu'}: ${formatDateTime(signature.proof.at)}, vydal ${escapeHtml(signature.proof.issuer)}.</div>`
  : signature.stamp_problem ? `<div class="meta">Časové razítko čas nepotvrzuje. ${escapeHtml(signature.stamp_problem)}</div>` : '';
const signedFilesLine = signature => {
  const names = signature.files.length ? signature.files.join(', ')
    : signature.covers_document ? 'celý dokument' : CONTAINER.kind === 'xades' ? '' : 'žádný soubor v\u00a0kontejneru';
  return names ? `<div class="meta">Podepisuje: ${escapeHtml(names)}</div>` : '';
};
const levelOf = signature => {
  const expired = signature.not_after && new Date(signature.not_after) < new Date(signature.proof?.at ?? Date.now());
  const revocation = window.zfoRevocation;
  return !signature.intact || revocation.revokedBeforeSigning(signature) ? 'bad'
    : (signature.trusted && !expired && !signature.modified && !signature.changes_unchecked && !revocation.revoked(signature)) ? 'ok' : 'warn';
};
// A time stamp can be intact while the signature it stamps is broken; it then proves nothing, so the card must not
// show it as a confirmed time.
const shownSignature = signature => signature.intact ? signature : {...signature, proof: null};
const signatureCard = original => {
  const signature = shownSignature(original);
  const revocation = window.zfoRevocation;
  const level = levelOf(signature);
  const state = !signature.intact ? (signature.content_changed ? 'Podpis je porušen – podepsaný obsah byl změněn'
                                     : 'Podpis je neplatný – ' + escapeHtml(signature.broken_reason))
    : revocation.state(signature) ? revocation.state(signature)
    : signature.modified ? 'Podepsaná verze je neporušená, ale po podpisu se změnilo víc než vyplněná pole'
    : signature.changes_unchecked ? 'Podepsaná verze je neporušená, ale změny po podpisu se nepodařilo zkontrolovat'
    : signature.trusted ? 'Podpis je platný, certifikát je důvěryhodný'
    : signature.unverifiable ? 'Obsah je neporušený, důvěryhodnost certifikátu se nepodařilo ověřit'
    : 'Obsah je neporušený, certifikát není hodnocen jako důvěryhodný';
  return `<div class="zfo-card ${level}" title="${escapeHtml(signature.reason || '')}">
    <div class="who">${icons[level]}${escapeHtml(signature.subject || 'Neznámý podepisující')}</div>
    <div class="state">${state}</div>
    ${signedFilesLine(signature)}
    ${signature.trust_source === 'eu' ? '<div class="meta">Vydavatel je v\u00a0důvěryhodném seznamu EU.</div>' : ''}
    ${signature.unverifiable ? `<div class="meta">${escapeHtml(signature.reason)}</div>` : ''}
    ${revocation.line(signature)}
    ${stampLine(signature)}
    ${!signature.intact && original.proof ? '<div class="meta">Podpis obsahuje časové razítko, u\u00a0neplatného podpisu ale nic nepotvrzuje.</div>' : ''}
    <div class="meta">Podepsáno: ${formatDateTime(signature.signed_at)}${signature.proof ? ' (uvádí podepisující)' : ''}${signature.issuer ? `<br>Vystavil: ${escapeHtml(signature.issuer)}` : ''}
      ${signature.not_after ? `<br>${new Date(signature.not_after) < new Date() ? 'Platnost certifikátu skončila' : 'Certifikát platí do'} ${new Date(signature.not_after).toLocaleDateString('cs-CZ')}` : ''}</div>
  </div>`;
};
const summary = signatures => {
  const broken = signatures.filter(signature => !signature.intact).length;
  if (!signatures.length) return CONTAINER.unsupported || 'Kontejner neobsahuje žádný podpis.';
  if (broken === 1) return 'Jeden podpis je porušený.';
  if (broken) return `Podpisy: ${broken} ${plural(broken, 'porušený', 'porušené', 'porušených')}.`;
  if (signatures.every(signature => levelOf(shownSignature(signature)) === 'ok')) return 'Všechny podpisy jsou platné.';
  return 'Podpisy jsou neporušené, ale ne všechny jsou plně ověřené.';
};
const signatures = CONTAINER.signatures;
const renderSignatures = () => {
  queryOne('[data-signatures]').innerHTML = signatures.length ? signatures.map(signatureCard).join('')
    : '<div class="zfo-empty">Kontejner neobsahuje žádný ověřitelný podpis.</div>';
  queryOne('[data-status]').textContent = summary(signatures);
  window.zfoRevocation.ask(SESSION, signatures, renderSignatures);
};

const actions = {
  ...fileView.actions,
  save: fileView.actions['save-file'],
  panel: () => {
    const hidden = document.body.classList.toggle('panel-hidden');
    queryOne('.zfo-bar [data-action="panel"]').classList.toggle('active', !hidden);
  },
};
for (const button of document.querySelectorAll('[data-action]')) {
  button.addEventListener('click', () => actions[button.dataset.action]());
}
window.zfoAction = name => actions[name]?.();

renderSignatures();
