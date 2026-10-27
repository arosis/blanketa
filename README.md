# Blanketa

Aplikace pro macOS na vyplňování a elektronické podepisování:
- **formulářů 602XML (`.zfo`)**, které jinak otevře jen Software602 Form Filler pro Windows (např. ČÚZK návrh na vklad, formuláře ČSSZ a krajů);
- **PDF formulářů (AcroForm)**.

Podepisuje se certifikátem z klíčenky macOS nebo z tokenu (CryptoTokenKit). Formuláře podepsané Blanketou otevře i Software602 Form Filler a podpis uzná.

## Co umí

**ZFO**
- Zobrazení formuláře ve vzhledu Form Filleru:
  - opakované sekce;
  - přepínané části (multiswitch);
  - výpočty XPath;
  - nápověda k polím;
  - vložená písma a obrázky;
  - uživatelská tlačítka formuláře.
- Uložit bez podpisu a Podepsat a uložit.
- Další funkce:
  - tisk (i do PDF), tisk jen vyplněných hodnot;
  - přepočítat a zkontrolovat formulář;
  - najít;
  - obnovit výchozí hodnoty pole nebo celého formuláře;
  - import a export dat (XML).
- Panel podpisů: neporušenost každé vrstvy a důvěryhodnost certifikátu podle macOS.

**PDF**
- Vyplňování přes PDF.js:
  - texty, zaškrtávátka, přepínače, seznamy;
  - ukládá se jako přírůstková aktualizace, takže dřívější podpisy zůstanou platné.
- Podpis PAdES (pyHanko):
  - do připraveného podpisového pole s razítkem;
  - nebo na místo, které uživatel vyznačí tažením myši;
  - nebo neviditelně.
- Tisk vyplněného souboru v plné kvalitě.

Technický popis formátu ZFO: [docs/zfo-format.md](docs/zfo-format.md).

## Architektura

```
build/Blanketa.app
├─ MacOS/Blanketa      Swift (macapp/main.swift): okna s WKWebView, menu, tisk, dialogy
└─ Resources/
   ├─ zfo-server/         Python (app.py) zabalený PyInstallerem; jeden proces na okno, lokální HTTP
   └─ macsign             Swift (macsign/macsign.swift): seznam identit, podpis a důvěra přes Security.framework
```

| Soubor | Úloha |
|---|---|
| `app.py` | HTTP server: převod FO → HTML (XSLT), relace formuláře, uložení, podpis, PDF |
| `zfoengine/` | port backendu zfo-editoru (Python 3, bez GTK): model, výpočty, opakované sekce, kontrola |
| `ui/shell.html` | rozhraní ZFO (lišta, panely, dialogy) vkládané do vykreslené stránky formuláře |
| `ui/pdf.html` | rozhraní PDF nad PDF.js |
| `ui/fo2html.xsl` | doplněk XSLT zfo-editoru (obrázky, rozměry) |
| `zfo_sign.py`, `zfo_verify.py` | podpis a ověření vrstev XMLDSig |
| `pdf_sign.py` | vyplnění a podpis PDF, ověření podpisů |
| `keychain.py` | klíč v klíčence nebo na tokenu přes `macsign` |

## Sestavení

Potřeba je Xcode Command Line Tools a Python 3.12+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
./build.sh
```

Výsledek je `build/Blanketa.app`, podepsaná ad hoc (bez notarizace). Na jiném Macu je ji proto poprvé potřeba otevřít přes pravé tlačítko → Otevřít.

## Vývoj

```bash
.venv/bin/python app.py 8602
```

Server ve vývojovém režimu nabídne vzorky ze `samples/` a `samples-pdf/` na http://localhost:8602.

Pozor: náhled v Chromu se liší od aplikace (WebKit). Rozvržení proto ověřujte nástrojem `tools/webkit-probe`:

```bash
swiftc -O tools/webkit-probe.swift -o tools/webkit-probe
```

Testy enginu:

```bash
.venv/bin/python -I tests/test_engine.py
```

## Testovací certifikát

```bash
tools/make-test-pki.sh
```

Skript vytvoří v `pki/` nedůvěryhodnou testovací CA, podpisový certifikát a klíčenku `pki/test.keychain-db` s heslem `test`. Aplikace tuto identitu nabízí s označením „(TEST)“. Sestavená aplikace ji hledá v `~/Library/Application Support/Blanketa/test.keychain-db`. Pro skutečné podání je potřeba kvalifikovaný certifikát v klíčence nebo na tokenu.

## Omezení

- Výpočty psané v JavaScriptu 602 se nevyhodnocují (výpočty XPath ano).
- Odesílání na portály (submit) není podporované.
- Formuláře s mnoha opakováními se otevírají pomalu (desítky sekund).
- PDF formuláře XFA nejsou podporované.
- Aplikace není notarizovaná.

## Licence a cizí kód

- `vendor/zfo-editor-src/` – zfo-editor, © CZ.NIC, GNU LGPL 2.0 nebo novější. `zfoengine/` je jeho upravený port pod stejnou licencí.
- `vendor/pdfjs/` – PDF.js 4.10.38, © Mozilla, Apache 2.0.
- Python knihovny podle `requirements.txt` mají vlastní licence (pyHanko MIT, pypdf BSD, lxml BSD…).

Přehled a podmínky jsou v [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Sestavená aplikace nese licence a zdroj `zfoengine/` v `Contents/Resources/Licence/`.
