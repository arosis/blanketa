# ASiC and XAdES verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blanketa opens ASiC-S/ASiC-E containers and standalone enveloping/enveloped XAdES files, verifies their XAdES signatures fail-closed, and shows the files (PDF preview, save, open a saved copy) with signature cards like PDF and ZFO.

**Architecture:** `xades_verify.py` checks one `ds:Signature` against a resolver for its external references and returns a description dict shaped like `pdf_sign.describe_signatures`. `asic_verify.py` reads the ZIP (or standalone XML), applies the container limits and rules, calls `xades_verify` per signature and derives per-file states. `app.py` sniffs the opened file, renders the new `ui/container.html` and serves entries by index. The hosts get one new message, `saveContainerFile`, which downloads the entry from the local server and writes it binary, optionally opening the saved copy.

**Tech Stack:** Python 3, lxml, cryptography, asn1crypto, pyHanko (RFC 3161 validation only), PDF.js (already vendored), Swift/WebKit, pywebview.

**Spec:** `docs/superpowers/specs/2026-11-08-asic-xades-verify-design.md` (Czech; read it in full, it fixes the rules, texts and states).

## Global Constraints

- Project rules in `/Users/bejr/Projects/blanketa/CLAUDE.md` and `.claude/skills/create-code-review/checklist.md` (§ Signature integrity, § Local server, § Three hosts, § Packaging, § Czech UI text) apply to every task.
- English code, identifiers and commits. Czech UI texts with Czech typography („…“, –, non-breaking space where the project uses one). No comments unless they give a non-obvious why. No `Co-Authored-By` trailer.
- Tests use only the `pki/` test identity (`pki/user.crt`, `pki/user.key`, made by `tools/make-test-pki.sh`) or throwaway keys generated in the test. Never real documents or qualified certificates.
- No new dependencies. Do not use `signxml` (present in `.venv` but not a project dependency).
- `zfo_verify.py` and `trustlist.py` are shared with a parallel session (#60): import from them, do not change them.
- Anything from a file reaches a page only via `app.json_for_script()`, `textContent` or `escapeHtml()`.
- Container limits: at most 1000 entries, at most 100 MB uncompressed in total, compression ratio at most 100:1 per entry; only stored or deflate; no encrypted entries; no duplicate names; no `..`, absolute or backslash names.
- Accepted algorithms (URIs exactly):
  - c14n: `http://www.w3.org/TR/2001/REC-xml-c14n-20010315` (+`#WithComments`), `http://www.w3.org/2001/10/xml-exc-c14n#` (+`WithComments`), `http://www.w3.org/2006/12/xml-c14n11` (+`#WithComments`, only when the input has no `xml:id`/`xml:base`, computed as c14n 1.0)
  - signature: `http://www.w3.org/2001/04/xmldsig-more#rsa-sha256|rsa-sha384|rsa-sha512`, `http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256|ecdsa-sha384|ecdsa-sha512`
  - reference digests: `http://www.w3.org/2001/04/xmlenc#sha256`, `http://www.w3.org/2001/04/xmldsig-more#sha384`, `http://www.w3.org/2001/04/xmlenc#sha512`, and `http://www.w3.org/2000/09/xmldsig#sha1` (references and `CertDigest` only)
  - transforms: the c14n URIs above, `http://www.w3.org/2000/09/xmldsig#enveloped-signature`, and `http://www.w3.org/2000/09/xmldsig#base64` (only standalone enveloping, as the only transform, on a `ds:Object` reference)
- Namespaces: XAdES `http://uri.etsi.org/01903/v1.3.2#` and `http://uri.etsi.org/01903/v1.4.1#`; ASiC `http://uri.etsi.org/02918/v1.2.1#`; SignedProperties type `http://uri.etsi.org/01903#SignedProperties`.
- File states (internal value → Czech label): `changed` „změněno po podpisu“, `signed` „podepsáno“, `invalid` „podpis neplatný“, `unverified` „neověřeno“, `unsigned` „nepodepsáno“, decided in that order.
- Broken-signature headline: content digest mismatch → „Podpis je porušen – podepsaný obsah byl změněn“; any other cause → „Podpis je neplatný – <reason>“.

## Review Focus

- Czech or percent-encoded file names (`P%C5%99%C3%ADloha.pdf`, NFD vs NFC) must resolve exactly and display correctly; NFC/NFD variants are different names (exact match only). → Task 3 test `test_percent_encoded_czech_name_resolves`.
- One of several signature files in an ASiC-E is malformed XML: the other signatures still verify, the bad file shows as one invalid signature card, not the damaged-file page. → Task 3 test `test_malformed_signature_file_is_one_invalid_card`.
- A container with no PDF, or only `META-INF` entries: the page still opens, preview shows the first file or an empty-list message. → Task 5 test `test_container_without_pdf_renders`, Task 6 browser check.
- A signature intact but by a certificate not on the EU lists: card says intact and not trusted, never "porušen". → Task 5 test `test_untrusted_intact_signature_is_not_broken`.
- ZIP directory entries (`folder/`) and the `mimetype` file are not listed as files and never count as unsigned data. → Task 3 test `test_directories_and_mimetype_are_not_listed`.

---

### Task 1: Test sample builder and core XAdES verification

**Files:**
- Create: `tests/asic_samples.py`
- Create: `xades_verify.py`
- Create: `tests/test_asic.py`
- Modify: `.github/workflows/tests.yml` (add `tests/test_asic.py` next to every `tests/test_trustlist.py` line, incl. the loop on line ~104)

**Interfaces:**
- Produces (`tests/asic_samples.py`, used by Tasks 2–7, `tools/selftest.py` and `tools/fuzz.py`):
  - `xades_signature(files: dict[str, bytes], *, signature_id="S1", digest=SHA256, signature_method=RSA_SHA256, c14n=C14N10, signing_certificate="v2" | "v1" | None, key=None, cert=None, stamp=False, manifest=False, mutate=None) -> etree._Element` — detached signature over `files` (URI = name, percent-encoded); `mutate(signature)` runs after building the unsigned skeleton and before computing digests/signature value, so hostile tests can inject elements; `key`/`cert` default to `pki/user.key`/`pki/user.crt`.
  - `signatures_xml(*signatures: etree._Element) -> bytes` — `asic:XAdESSignatures` root.
  - `asic_e(files: dict[str, bytes], signature_files: dict[str, bytes], *, mimetype=True, manifest=True) -> bytes`
  - `asic_s(name: str, data: bytes, signature_file: bytes, *, kind="xades") -> bytes` (`kind="cades"` writes `META-INF/signature.p7s` with arbitrary bytes)
  - `enveloping(data: bytes, mime: str, *, base64_transform: bool) -> bytes`, `enveloped(document: etree._Element) -> bytes`
  - constants `SHA1, SHA256, MD5, RSA_SHA1, RSA_SHA256, C14N10, C14N11, EXC_C14N, ECDSA_SHA256` (algorithm URIs)
- Produces (`xades_verify.py`):
  - `class XadesError(ValueError)` — the file cannot be read as a signature file at all.
  - `parse(data: bytes) -> etree._Element` — hardened parser: `etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)`; raises `XadesError` when the document has a DOCTYPE (`tree.docinfo.doctype`) or does not parse.
  - `find_signatures(root) -> list[etree._Element]` — `root` itself if it is `ds:Signature`, else its direct `ds:Signature` children (and for enveloped, the direct child of the document root).
  - `verify(signature, resolve, *, allow_empty_uri=False, standalone=False) -> dict` where `resolve(uri: str) -> tuple[str, bytes] | None` maps a non-`#` URI (raw attribute value, `None` for a missing URI) to `(display name, bytes)` or `None` when missing/forbidden. Returns:
    `{"subject", "issuer", "publisher": False, "intact": bool, "reason": str, "content_changed": bool, "modified": False, "changes_unchecked": False, "not_after", "signed_at", "der", "stamps": [], "references": [{"name": str, "outcome": "ok" | "changed" | "refused"}]}`
    `reason` is "" when intact; `content_changed` is True when a file or SignedProperties digest did not match (drives the headline). When no certificate can be found, `subject`/`issuer` are "" and `der` is `None`.
  - Czech reason texts as module constants, e.g. `UNKNOWN_SIGNATURE_ALGORITHM = "neznámý algoritmus podpisu"`, `UNKNOWN_DIGEST = "neznámý nebo nepovolený algoritmus otisku"`, `UNKNOWN_TRANSFORM = "nepovolená transformace"`, `UNSUPPORTED_C14N = "nepodporovaná kanonizace"`, `WRAPPED = "podpis obsahuje duplicitní nebo cizí prvky"`, `BAD_REFERENCE = "odkaz na soubor mimo kontejner nebo na chybějící soubor"`, `NO_SIGNED_PROPERTIES = "chybí podepsané vlastnosti XAdES"`, `CERTIFICATE_MISMATCH = "certifikát podepisujícího nesouhlasí s podepsaným otiskem"`, `BAD_VALUE = "hodnota podpisu nesouhlasí s certifikátem"`.
- Consumes: `zfo_verify.DS`, `zfo_verify.C14N`, `zfo_verify.EXC` (constants only).

- [ ] **Step 1: Write `tests/asic_samples.py`.** Build XML with lxml, digests with hashlib, signature value with `cryptography` (RSA PKCS#1 v1.5 / ECDSA DER→raw r||s per XML-DSig). SignedProperties holds `SigningTime` (fixed `2026-01-15T10:00:00Z`) and `SigningCertificateV2`/`SigningCertificate` with SHA-256 (v2) or SHA-1 (v1) `CertDigest`; `QualifyingProperties Target="#S1"` inside one `ds:Object`. The SignedProperties reference has `Type="http://uri.etsi.org/01903#SignedProperties"` and an exclusive-c14n transform.
- [ ] **Step 2: Write failing tests in `tests/test_asic.py`** (`unittest`, runnable as `.venv/bin/python -I tests/test_asic.py`, `ZFO_OFFLINE=1` like the other test files). Use a dict resolver `lambda uri: (uri, files[uri]) if uri in files else None`.

```python
def test_intact_signature_verifies(self):
    result = xades_verify.verify(asic_samples.xades_signature(FILES), resolver(FILES))
    self.assertTrue(result["intact"]); self.assertEqual(result["reason"], "")
    self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "ok"}])
    self.assertEqual(result["signed_at"], "2026-01-15T10:00:00+00:00")
def test_changed_file_breaks_signature(self):        # outcome "changed", content_changed True, intact False
def test_changed_signed_properties_break_signature(self)   # mutate SigningTime after signing → content_changed True
def test_changed_signature_value_is_invalid(self)          # reason BAD_VALUE, content_changed False
def test_missing_signing_certificate_is_invalid(self)      # signing_certificate=None → NO_SIGNED_PROPERTIES or CERTIFICATE_MISMATCH
def test_v1_signing_certificate_with_sha1_verifies(self)   # signing_certificate="v1" → intact
def test_sha1_reference_digest_verifies(self)              # digest=SHA1 → intact
def test_md5_digest_is_invalid(self)                       # reason UNKNOWN_DIGEST
def test_rsa_sha1_signature_is_invalid(self)               # reason UNKNOWN_SIGNATURE_ALGORITHM
def test_ecdsa_signature_verifies(self)                    # throwaway P-256 key + self-signed cert
def test_signed_manifest_references_are_checked(self)      # manifest=True, change a file → outcome "changed"
def test_c14n11_without_xml_id_verifies(self)
def test_c14n11_with_xml_id_is_unsupported(self)           # reason UNSUPPORTED_C14N
def test_doctype_is_refused(self)                          # parse(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>') raises XadesError
```

- [ ] **Step 3: Run** `.venv/bin/python -I tests/test_asic.py` — expected: errors, `xades_verify` missing.
- [ ] **Step 4: Implement `xades_verify.py`** following spec § „Ověření jednoho podpisu“ steps 1–6 in order; the first failure sets `reason`. `#id` references resolve only to the single element with that `Id` among descendants of this `ds:Signature`; any duplicated `Id` value anywhere in the signature's document → `WRAPPED`. Exactly one SignedProperties reference must exist and point to the `SignedProperties` inside the single `QualifyingProperties` whose `Target` is `#` + the signature's `Id`. Certificate: the first `ds:X509Certificate` whose digest matches the first `Cert/CertDigest` of `SigningCertificateV2` (else `SigningCertificate`). Keep reference outcome collection going after the first failure so all file outcomes are known.
- [ ] **Step 5: Run** `.venv/bin/python -I tests/test_asic.py` — expected: all pass. Run `.venv/bin/ruff check xades_verify.py tests/asic_samples.py tests/test_asic.py`.
- [ ] **Step 6: Commit** `git commit -m "Verify XAdES signatures fail-closed (#63)"`

### Task 2: Signature time stamps

**Files:**
- Modify: `xades_verify.py`, `tests/asic_samples.py`, `tests/test_asic.py`

**Interfaces:**
- Consumes: Task 1 `verify`, `asic_samples.xades_signature(stamp=True)`.
- Produces: `verify()` fills `stamps` with dicts identical in shape to `pdf_sign._stamp_details(status, "signature")`: `{"kind": "signature", "intact", "at", "issuer", "der"}`, so `app.judge_time_stamps` works unchanged.

- [ ] **Step 1: Extend `asic_samples`** so `stamp=True` adds `UnsignedProperties/UnsignedSignatureProperties/SignatureTimeStamp/EncapsulatedTimeStamp` with a token from `pyhanko.sign.timestamps.DummyTimeStamper` (throwaway TSA cert, like `tools/make-timestamp-samples.py`) over SHA-256 of the c14n-1.0 `ds:SignatureValue`; `stamp="wrong"` stamps other bytes.
- [ ] **Step 2: Write failing tests**

```python
def test_time_stamp_is_reported(self):
    stamp, = xades_verify.verify(asic_samples.xades_signature(FILES, stamp=True), resolver(FILES))["stamps"]
    self.assertEqual((stamp["kind"], stamp["intact"]), ("signature", True)); self.assertIsNotNone(stamp["der"])
def test_time_stamp_over_other_data_is_not_intact(self)   # stamp="wrong" → intact False, signature itself still intact
def test_damaged_time_stamp_token_is_not_intact(self)     # flip bytes in EncapsulatedTimeStamp → intact False, no exception
```

- [ ] **Step 3: Run** — expected FAIL (`stamps` empty).
- [ ] **Step 4: Implement** with `pyhanko.sign.validation.generic_cms.validate_tst_signed_data` (run via `asyncio.run`, `ValidationContext(allow_fetching=False)`, expected imprint = digest of the c14n of `ds:SignatureValue` using the stamp's `ds:CanonicalizationMethod` or c14n 1.0), wrap the result in `TimestampSignatureStatus` and pass it to `pdf_sign._stamp_details(status, "signature")`. Any exception → `_stamp_details(None, "signature")`. Ignore `CertificateValues`, `RevocationValues`, archive stamps.
- [ ] **Step 5: Run** `.venv/bin/python -I tests/test_asic.py` — PASS.
- [ ] **Step 6: Commit** `git commit -m "Report XAdES signature time stamps (#63)"`

### Task 3: Containers and standalone XAdES

**Files:**
- Create: `asic_verify.py`
- Modify: `tests/test_asic.py`

**Interfaces:**
- Consumes: `xades_verify.parse`, `find_signatures`, `verify`, `XadesError`.
- Produces (`asic_verify.py`, used by Tasks 4–5):
  - `MAX_ENTRIES = 1000`, `MAX_TOTAL_SIZE = 100 * 1024 * 1024`, `MAX_RATIO = 100`
  - `class ContainerError(ValueError)` — damaged container (→ DAMAGED_PAGE).
  - `is_signed_file(data: bytes) -> bool` — ZIP with `META-INF/*signatures*.xml`, `META-INF/signature.p7s`, `META-INF/timestamp.tst` or `META-INF/evidencerecord.*` and without `form.fo`; or XML whose signatures carry XAdES `QualifyingProperties` (a plain `.fo` ZFO form must return False).
  - `describe(data: bytes) -> tuple[dict, list[bytes]]` — description and entry contents in the same order as `description["files"]`:
    `{"kind": "asic-s" | "asic-e" | "xades", "label": "ASiC-E s podpisy XAdES" | …, "files": [{"name": str, "size": int, "state": str, "pdf": bool}], "signatures": [verify() dicts plus "files": [names]], "warnings": [Czech str], "unsupported": "" | Czech note}`
    Broken signature files (`XadesError`) contribute one signature dict with `intact False`, `reason "soubor s podpisem nejde přečíst"`, empty subject. The `references` key is dropped from the output after states are derived.
  - `uri_to_name(uri: str | None, names: set[str], *, single: str | None) -> str | None` — percent-decode, strip one leading `/`, exact match; `None` URI → `single`; refuse absolute URIs, `..`, `\`, `META-INF/` except exactly `META-INF/manifest.xml`.

- [ ] **Step 1: Write failing tests**

```python
def test_asic_e_lists_files_with_states(self):
    data = asic_samples.asic_e({"smlouva.pdf": PDF, "cenik.xlsx": b"x", "poznamka.txt": b"y"},
        {"META-INF/signatures001.xml": signatures_xml(xades_signature({"smlouva.pdf": PDF, "cenik.xlsx": b"x"}))})
    description, contents = asic_verify.describe(data)
    self.assertEqual([(f["name"], f["state"]) for f in description["files"]],
                     [("smlouva.pdf", "signed"), ("cenik.xlsx", "signed"), ("poznamka.txt", "unsigned")])
    self.assertEqual(description["signatures"][0]["files"], ["smlouva.pdf", "cenik.xlsx"])
def test_changed_file_is_changed_and_others_invalid(self)    # change cenik.xlsx inside the ZIP → cenik "changed", smlouva "invalid"
def test_second_intact_signature_keeps_file_signed(self)      # two signatures; one broken by value → file still "signed"
def test_asic_s_reference_without_uri(self)                   # ASiC-S, single file, URI omitted → "signed"
def test_cades_container_is_unverified(self)                  # asic_s(kind="cades") → state "unverified", unsupported note set, no signatures
def test_manifest_xml_reference_is_allowed_and_hidden(self)
def test_reference_into_meta_inf_is_refused(self)             # → signature intact False, reason BAD_REFERENCE
def test_enveloping_base64_content_is_listed(self)            # name "podepsaný obsah.pdf", state "signed", contents == original bytes
def test_enveloped_document_verifies(self)                    # files == [] , signature intact
def test_plain_zfo_is_not_a_signed_file(self)                 # is_signed_file(samples/mac-vyplneny/form.zfo bytes) is False, also form.fo inside
def test_percent_encoded_czech_name_resolves(self)            # "Příloha.pdf" and NFD "Příloha.pdf" both present; only the referenced one is "signed"
def test_malformed_signature_file_is_one_invalid_card(self)
def test_directories_and_mimetype_are_not_listed(self)
def test_rule_breaks_are_warnings(self)                       # missing manifest.xml / mimetype not first → warnings non-empty, signature intact
```

- [ ] **Step 2: Run** — expected FAIL (module missing).
- [ ] **Step 3: Implement `asic_verify.py`.** Check every `ZipInfo` before reading (`compress_type in (ZIP_STORED, ZIP_DEFLATED)`, `flag_bits & 1 == 0`, name rules, ratio `file_size <= MAX_RATIO * max(compress_size, 1)`), cap total by summing declared sizes and reading with a counting limit (a lying header must not get past 100 MB). Kind: `mimetype` content first, else ASiC-S when exactly one data file and `META-INF/signatures.xml`, else ASiC-E. States from all signatures' `references` in the order of the Global Constraints.
- [ ] **Step 4: Run** `.venv/bin/python -I tests/test_asic.py` — PASS; ruff clean.
- [ ] **Step 5: Commit** `git commit -m "Read ASiC containers and standalone XAdES files (#63)"`

### Task 4: Hostile inputs, fuzzing, selftest

**Files:**
- Modify: `tests/test_security.py`, `tools/fuzz.py`, `tools/selftest.py`, `.github/workflows/tests.yml` (add `asic` to the `--check` loop, line ~100)

**Interfaces:**
- Consumes: `asic_samples.*`, `asic_verify.describe`, `asic_verify.ContainerError`, `xades_verify.XadesError`.

- [ ] **Step 1: Write the hostile tests** in a new `class ContainerAttacks(unittest.TestCase)` in `tests/test_security.py`, each asserting no signature in the result is `intact` (or `ContainerError` is raised where named):
  - `test_wrapped_signed_properties_are_refused` — a second `SignedProperties` with the same `Id` outside `QualifyingProperties`, original changed
  - `test_second_object_with_qualifying_properties_is_refused`
  - `test_duplicate_id_is_refused`
  - `test_changed_file_in_container_is_refused`
  - `test_unknown_transform_is_refused` (XPath and XSLT URIs)
  - `test_unknown_signature_algorithm_is_refused`
  - `test_forged_signing_certificate_digest_is_refused` (`KeyInfo` cert swapped for `pki/ca.crt`)
  - `test_external_entity_is_not_read` (`<!DOCTYPE … SYSTEM "file:///etc/passwd">` → `XadesError`, and `OUTSIDE_FILE_TEXT` never appears in the description)
  - `test_entity_expansion_is_refused` (billion laughs → `XadesError`, returns in < 1 s)
  - `test_reference_escaping_the_container_is_refused` for `../x`, `file:///etc/passwd`, `http://example.com/x`, `/META-INF/signatures.xml`
  - `test_zip_bomb_is_refused` (one deflated entry of 101 MB zeros → `ContainerError`)
  - `test_duplicate_zip_names_are_refused`, `test_encrypted_entry_is_refused`, `test_too_many_entries_are_refused` (1001 entries)
- [ ] **Step 2: Run** `.venv/bin/python -I tests/test_security.py` — the new tests pass (Tasks 1–3 implement the rules); fix the implementation where one fails.
- [ ] **Step 3: Add fuzz target** `"asic": (fuzz_asic, asic_seeds)` to `TARGETS` in `tools/fuzz.py`: `fuzz_asic(data)` calls `asic_verify.describe(data)` and tolerates only `ContainerError`/`XadesError`; `asic_seeds()` yields an ASiC-E, an ASiC-S and an enveloping XAdES built with `tests/asic_samples.py` (add `ROOT / "tests"` to `sys.path` as needed).
- [ ] **Step 4: Run** `.venv/bin/python -I tools/fuzz.py --check asic` then `.venv/bin/python -I tools/fuzz.py asic 60` — expected: no findings.
- [ ] **Step 5: Extend `tools/selftest.py`**: build an ASiC-E signed with the selected identity's key and cert (via `asic_samples.xades_signature(key=…, cert=…)`), assert `describe` shows it intact, change one byte of the data file, assert `changed`; exit non-zero otherwise.
- [ ] **Step 6: Run** `.venv/bin/python -I tools/selftest.py test` — expected exit 0.
- [ ] **Step 7: Commit** `git commit -m "Test hostile ASiC and XAdES inputs, fuzz and selftest them (#63)"`

### Task 5: Server: container page and entry route

**Files:**
- Modify: `app.py`, `tests/test_app.py`
- Create: `ui/container.html` (minimal placeholder with the four markers; Task 6 builds the UI)

**Interfaces:**
- Consumes: `asic_verify.is_signed_file`, `asic_verify.describe`, `asic_verify.ContainerError`, `add_trust_verdicts`, `new_session`, `json_for_script`, `data_file_folder`.
- Produces:
  - `render_container_page(name: str) -> str` — session `{"name": name, "entries": contents}`; replaces `__SESSION__`, `__FILE__` (file name), `__CONTAINER__` (description after `add_trust_verdicts(description["signatures"])`), `__FOLDER__` (`data_file_folder(session)`), all via `json_for_script` except the session token.
  - `CONTAINER_PAGE` loaded like `PDF_PAGE`.
  - `GET /container-file?s=<session>&i=<index>` → 404 on unknown session or bad index; body = entry bytes; `application/pdf` when the entry starts with `%PDF-`, else `application/octet-stream` with `Content-Disposition: attachment; filename*=UTF-8''<percent-encoded basename>`.
  - `_serve_form_page`: `.pdf` → PDF page; elif `asic_verify.is_signed_file(bytes)` → container page; else ZFO. `ContainerError`/`XadesError` fall into the existing DAMAGED_PAGE branch.

- [ ] **Step 1: Write failing tests** in `tests/test_app.py` (follow the existing server-test pattern in that file):

```python
def test_container_page_carries_files_and_signatures_as_json(self)
def test_container_file_route_needs_the_session(self)       # no/unknown s → 404
def test_container_file_route_serves_by_index(self)         # i=0 → PDF bytes, application/pdf; i=99 → 404; i=-1 → 404
def test_hostile_file_name_stays_json(self)                 # entry named '</script><img src=x onerror=alert(1)>.txt' → not present raw in the HTML
def test_container_without_pdf_renders(self)
def test_untrusted_intact_signature_is_not_broken(self)     # signature dict intact True, trusted False
def test_damaged_container_shows_the_damaged_page(self)     # truncated ZIP → 422
```

- [ ] **Step 2: Run** `.venv/bin/python -I tests/test_app.py` — new tests FAIL.
- [ ] **Step 3: Implement** in `app.py` as listed in Interfaces.
- [ ] **Step 4: Run** `.venv/bin/python -I tests/test_app.py` and `tests/test_security.py` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Serve ASiC containers and their files (#63)"`

### Task 6: Container window (layout B)

**Files:**
- Modify: `ui/container.html`

**Interfaces:**
- Consumes: Task 5 markers and route; `ui/bridge.js` (`window.zfoNative`); PDF.js under `/pdfjs/` exactly as `ui/pdf.html:149-180` loads it; `window.zfoRevocation.ask` as in `ui/pdf.html:285`.
- Produces: page posts `{action: 'saveContainerFile', url: '/container-file?s=…&i=…', name, folder, open: false|true}` (Task 7 handles it).

- [ ] **Step 1: Build the page** per spec § „Okno kontejneru“: CSS from the first `<style>` of `ui/shell.html` (as `ui/pdf.html` does); toolbar „Uložit soubor…“ (primary) and „Otevřít v Blanketě“ (enabled only for PDF/ZFO names and only when `window.zfoNative` exists); centre: read-only PDF.js viewer (`annotationMode: AnnotationMode.ENABLE`, no form storage, no save) for `pdf: true` entries, otherwise a card with icon, name, size, state chip and „Uložit soubor…“; right panel: „Kontejner“ (label, counts „Soubory: 3, z toho 1 nepodepsaný“, warnings, `unsupported` note), „Soubory v kontejneru“ list with state chips (labels from Global Constraints), „Elektronické podpisy“ cards copied from `ui/pdf.html` `signatureCard`/`stampCard` with identical texts, plus the headline rule for `reason`/`content_changed` and a „Podepisuje:“ line; status bar with the file name and a summary („Všechny podpisy jsou platné.“ / „Jeden podpis je porušený.“ / „Podpisy: 2 porušené.“). Initial selection: first PDF, else first file, else the message „Kontejner neobsahuje žádné soubory.“ All file-derived text via `textContent`/`escapeHtml`. Without `zfoNative` (web), „Uložit soubor…“ is an `<a download>` to the entry URL.
- [ ] **Step 2: Verify in the browser pane.** Write two sample containers with `tests/asic_samples.py` into the scratchpad (one ASiC-E with a PDF, a changed xlsx and an unsigned txt; one CAdES ASiC-S), start the server (`app.py --file <path> <port>` via a `.claude/launch.json` entry), open it with `preview_start`, check with `read_page`: the file list states, the card texts, the PDF preview pages render, selecting the xlsx shows the save card, no console errors (`read_console_messages`). Take a screenshot as proof.
- [ ] **Step 3: Commit** `git commit -m "Show ASiC containers with a PDF preview and signature cards (#63)"`

### Task 7: Hosts: extend saveAttachment to container files and "open the saved copy"

**Files:**
- Modify: `desktop.py`, `macapp/main.swift`, `tests/test_security.py`

**Interfaces:**
- Consumes: the `saveAttachment` message from #60: `{action: 'saveAttachment', files: [{url, name}], folder: bool}`; `desktop.ATTACHMENT_URL`, `attachment_url_is_allowed`, `is_plain_file_name`, `Bridge._save_attachments`/`_save_downloads`; `AttachmentRequest` and `saveAttachments` in main.swift.
- Produces: `saveAttachment` also accepts URLs matching `/container-file\?s=[A-Za-z0-9_-]+&i=\d+` (exact, fullmatch, both hosts), and an optional `open: true` (only with `folder: false` and exactly one file) that opens the saved file in a new window after a successful save (`desktop`: `self._app.open_window(saved)`; mac: `onOpen?(saved)` on the main queue). A cancelled dialog opens nothing.
- Also: `OPEN_DIALOG_FILE_TYPES` first entry „Formuláře, PDF a kontejnery ASiC (*.zfo;*.fo;*.pdf;*.asice;*.sce;*.asics;*.scs)“ (keep any #60 types already in it); the mac open panel adds `asice`, `sce`, `asics`, `scs` and `.xml`.

- [ ] **Step 1: Failing tests** in `tests/test_security.py` next to the #60 attachment tests: container URL accepted; `/container-file?s=x&i=1&j=2`, `//evil/container-file?…`, `http://…` refused; `open: true` → `open_window` called once with the saved path; `open: true` with cancelled dialog → not called; `open: true` with `folder: true` or two files → refused (no dialog).
- [ ] **Step 2: Run** `.venv/bin/python -I tests/test_security.py` — FAIL.
- [ ] **Step 3: Implement** in `desktop.py` and `macapp/main.swift`.
- [ ] **Step 4: Run** tests — PASS; `swiftc -typecheck` main.swift with build.sh's frameworks.
- [ ] **Step 5: Commit** `git commit -m "Save container files and open a saved copy through saveAttachment (#63)"`

### Task 7b: One layout for containers and ISDS messages

**Files:**
- Create: `ui/files.js`
- Modify: `ui/container.html`, `ui/message.html`, `app.py` (route `/files.js`; `/container-file` → always octet-stream + nosniff + attachment), `tests/test_app.py`

**Interfaces:**
- Consumes: Task 7 `saveAttachment` with `open`; `/attachment` and `/container-file` routes.
- Produces: `ui/files.js` exposing one function, e.g. `window.zfoFiles({session, files: [{name, size, url, pdf, main?, state?, stateLabel?}], folder, list: Element, stage: Element, toolbar: {save, saveAll, open, zoomIn, zoomOut, zoom}, initial: index})` that renders the file rows into `list`, the read-only PDF.js preview or the file card into `stage`, wires the toolbar and posts `saveAttachment` (`{files, folder: false|true, open}`); web fallback `<a download>`; handles `zfoAttachmentSaved`/`zfoAttachmentFailed`.

- [ ] **Step 1: Failing tests** in `tests/test_app.py`: `/files.js` served as text/javascript without a session; `/container-file` for a PDF entry now returns `application/octet-stream` with `nosniff`; the message page still keeps file-derived text as JSON.
- [ ] **Step 2: Move** list/preview/save code from `ui/container.html` into `ui/files.js`; container page uses it (behaviour unchanged, save via `saveAttachment`).
- [ ] **Step 3: Rebuild `ui/message.html`** to the spec § „Sjednocení s oknem datové zprávy“ layout using `ui/files.js`; seal card, submission line, notice and receipt events keep their texts.
- [ ] **Step 4: Verify in the browser pane** with a container sample and with `samples-isds/odeslana.zfo` and `samples-isds/dorucenka.zfo`: list in the right panel, PDF preview in the centre (main document first), receipt shows events, no console errors; screenshot.
- [ ] **Step 5: Run** `tests/test_app.py`, `tests/test_security.py` — PASS.
- [ ] **Step 6: Commit** `git commit -m "Show ISDS messages and ASiC containers in one layout with a file preview (#63)"`

### Task 8: Packaging and file associations

**Files:**
- Modify: `build.sh`, `tools/build_desktop.py`, `macapp/Info.plist`, `packaging/windows/blanketa.iss`, `packaging/linux/blanketa.desktop`, `packaging/linux/blanketa-mime.xml`

- [ ] **Step 1: Bundle the new runtime files**: make sure `asic_verify.py`, `xades_verify.py` (picked up by PyInstaller through `app.py`'s imports — verify) and `ui/container.html` are included wherever `ui/pdf.html` is (grep both build scripts for `ui`).
- [ ] **Step 2: Associations** per spec § „Registrace typů souborů“: Info.plist `UTImportedTypeDeclarations` for `application/vnd.etsi.asic-e+zip` (`asice`, `sce`) and `application/vnd.etsi.asic-s+zip` (`asics`, `scs`) conforming to `public.zip-archive`, plus a `CFBundleDocumentTypes` entry with role Viewer and `LSHandlerRank` Owner; Inno Setup `Blanketa.asic` ProgID with `OpenWithProgids` for all four extensions always, and the default only under a new task `asicdefault` „Otevírat kontejnery ASiC (.asice, .asics) v Blanketě“; Linux MIME types with globs and both types in `MimeType=`.
- [ ] **Step 3: Verify**: `plutil -lint macapp/Info.plist`; `xmllint --noout packaging/linux/blanketa-mime.xml`; `./build.sh` produces the app and opening a sample `.asice` with `open -a` the built app shows the container window.
- [ ] **Step 4: Commit** `git commit -m "Register ASiC containers with Blanketa on all platforms (#63)"`

### Task 9: Full checks

- [ ] **Step 1: Run every check from CLAUDE.md § Checks plus the new file**, expecting all green:

```bash
.venv/bin/python -I tests/test_engine.py
.venv/bin/python -I tests/test_security.py
.venv/bin/python -I tests/test_app.py
.venv/bin/python -I tests/test_trustlist.py
.venv/bin/python -I tests/test_revocation.py
.venv/bin/python -I tests/test_asic.py
.venv/bin/python -I tools/fuzz.py pdf 60
.venv/bin/python -I tools/fuzz.py asic 60
.venv/bin/python -I tools/selftest.py test
python3 -m unittest discover -s .claude/skills/create-code-review/tests
```

- [ ] **Step 2:** Tell the user `tests/test_asic.py` and `fuzz.py asic` should be added to their local CLAUDE.md check list (it is untracked, not part of the PR).
