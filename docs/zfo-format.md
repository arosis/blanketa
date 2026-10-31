# Formát ZFO (Software602 602XML Form)

Technický popis formátu tak, jak jsme ho poznali při stavbě aplikace „Blanketa“.

Software602 formát veřejně nepopisuje. Vše níže vychází z rozboru skutečných formulářů a z vyplnění a podpisu ve Form Filleru na Windows. Ověřeno bylo i opačným směrem: soubory vytvořené touto aplikací Form Filler otevřel a podpis uznal. Kde jde o odhad, je to výslovně uvedeno.

Veřejné zdroje:
- registrace typu u IANA: [`application/vnd.software602.filler.form-xml-zip`](https://www.iana.org/assignments/media-types/application/vnd.software602.filler.form-xml-zip) (ZFO) a [`application/vnd.software602.filler.form+xml`](https://www.iana.org/assignments/media-types/application/vnd.software602.filler.form+xml) (FO);
- příručky Software602 (uživatelská příručka Form Filleru, příručka návrháře, „XPath výrazy“), kopie jsou jen lokálně v `docs-src/`;
- projekt zfo-editor (CZ.NIC, 2011, LGPL 2.0+), jehož XSLT a logika formulářů jsou základem `zfoengine/`. Zdroj je v `vendor/zfo-editor-src/`.

## 1. Kontejner

`.zfo` je ZIP s těmito položkami:

| Položka | Obsah |
|---|---|
| `form.fo` | celý formulář: vzhled, datový model, logika, podpisy (XML) |
| `summary/summary.xml` | popis pro správce souborů: `summary/@title`, `@author` aj. |
| `thumbnails/…` | náhledy (nepovinné) |

Samostatný soubor `.fo` je totéž XML bez ZIPu.

## 2. Vrstvy podpisů v `form.fo`

`form.fo` je řada vnořených **obalujících** podpisů XMLDSig (enveloping):

```
dsig:Signature                       ← nejvnější vrstva (např. uživatel)
  dsig:SignedInfo
    dsig:Reference URI="#<objId>"    ← jediná reference, na Object níže
  dsig:SignatureValue Id="_<objId>"
  dsig:KeyInfo/X509Data              ← jeden certifikát
  dsig:Object Id="<objId>" MimeType="text/xml"
    dsig:Signature                   ← další vrstva (Software602)
      …
        dsig:Object Id=…             ← nejvnitřnější: vlastní formulář (fo:root…)
    dsig:SignatureProperties         ← čas podpisu této vrstvy
      dsig:SignatureProperty Target="#<Id podpisu>"
        fm:systeminfo datetime="…"
```

- **Kanonizace:** reference používá inkluzivní c14n (`REC-xml-c14n-20010315`), `SignedInfo` exkluzivní c14n.
- **Algoritmus:** Form Filler sám podepisuje RSA-SHA1. Podpis RSA-SHA256 přijme stejně (ověřeno).
- **Vrstvy Software602:** typicky dvě nebo tři vrstvy s certifikáty „License Key“ a „Publication Key“. Poznají se podle `602XML` v subjektu certifikátu. Jsou to podpisy vydavatele formuláře, ne uživatele.
- **Přidání vrstvy:** novou vnější vrstvou se vnitřní podpisy nemění, zůstávají platné.
- **Opakovaný podpis:** při novém podpisu se uživatelské vrstvy sloupnou až k nejvrchnější vrstvě Software602 a podepisuje se znovu (`app.py: publisher_layer`).

## 3. Kam patří vyplněná data

Toto je hlavní zjištění z referenčního souboru vyplněného ve Form Filleru:

- Vyplněná data jsou kopie `form:model/form:instance/*` s hodnotami.
- Vkládají se jako **`dsig:Object` bez atributu `Id`** na konec **nejvrchnější vrstvy Software602**. Ta ho nereferencuje, takže její podpis zůstane platný.
- Uživatelský podpis pak obalí celek včetně dat, takže data jsou podepsaná uživatelem.
- Při uložení bez podpisu se starý datový `Object` nahradí novým (`app.py: filled_form`).

## Časová razítka

Software602 uvádí, že formulář lze opatřit časovým razítkem, ale způsob uložení nepopisuje a žádný dostupný vzorek razítko neobsahuje. `dsig:SignatureProperties` nese jen čas uvedený podepisujícím, proto ho panel podpisů nepovažuje za potvrzený.

## 4. Datový model a vazby

- **Instance:** `form:model/form:instance` je výchozí XML dat. Prvky mají vlastní jmenný prostor formuláře, např. `d:`.
- **Vazby:** `form:bind` s atributy `nodeset`, `calculate`, `required` a `constraint` (výrazy XPath 1.0 s rozšířeními 602).
- **Id vazby není název prvku.** Například pole s vazbou `o1_check_adresa_pre_dorucovani` zapisuje do `d:check_adresa_pre_dorucovani`. Spojení se hledá přes `nodeset`.
- **Opakované sekce:** odpovídají opakujícím se prvkům instance. Ve vzhledu je obalují tabulky s třídou `602-repetition-table`.
- **Kontrola (Zkontrolovat):** povinná pole (`required`) a `constraint` se vyhodnocují pro každý uzel zvlášť.
- **Výpočty:** `calculate` a `constraint` jsou XPath, nebo blok JavaScriptu 602 ve složených závorkách, který předá výsledek funkcí `Result`. Bloky provádí `zfoengine/js602.py`, funkce sdílené formulářem jsou ve `form:script`. Oficiální popis tohoto jazyka se nepodařilo najít, chování vychází ze skutečných formulářů. Dva předpoklady: když skript zavolá `Result` víckrát, platí poslední hodnota; a když formulář definuje vlastní funkci `RegExp`, `new RegExp` dál znamená vestavěný konstruktor.

## 5. Vzhled (XSL-FO s rozšířeními 602)

Vzhled je XSL-FO (`fo:root`, `fo:simple-page-master`, tabulky, bloky). Pole jsou prvky `fm:…` navázané na vazby. Form Filler se drží těchto pravidel; HTML převod je musí napodobit:

| Pravidlo | Chování Form Filleru |
|---|---|
| Tabulky | všechny jsou `table-layout="fixed"` bez šířky, tedy 100 % dostupné šířky po odečtení okrajů |
| Okraje | okraj bez barvy je černý (ne `currentColor`) |
| Buňky | výchozí padding je 0 |
| Písmo | výchozí Tahoma; pole s `font-family` v buňce dědí písmo buňky |
| Barvy polí | z `fm:formparams` (`proprequiredformfieldbkcolor`, `proproformfieldbkcolor`) |
| Rozměr stránky | první `fo:simple-page-master` |
| Multiswitch | prázdná hodnota zobrazí **první** `case` |
| Opakované sekce | tlačítka jsou aplikační události `addsect`/`delsect`; prázdný koncový sloupec tabulky opakování se nezobrazuje |
| Nápověda k poli | atribut `form602tooltip` |
| Obrázky | `fm:internal-graphic` v base64 (také **BMP**), rozměr z obalujícího `fo:instream-foreign-object` |
| Vložená písma | `fm:fonts/fm:font` (TTF v base64) |

## 6. Nastavení formuláře a uživatelská tlačítka

`fm:formsettings` obsahuje mimo jiné tlačítka formuláře `fm:userbutton<N>`:

| Atribut | Význam |
|---|---|
| `caption`, `tooltip` | popisek a nápověda |
| `iconid`, `showtext` | ikona (`print`, `saveform`, `savedata`, `loaddata`…) a zda ukázat text |
| `express` | výraz, typicky `LastParam(SendAppEvent(checkdata), SendAppEvent(print, 2), …)` |

`SendAppEvent` volá funkce aplikace:
- `checkdata` – kontrola;
- `print` – tisk; `print, 2` tiskne jen vyplněné hodnoty;
- `saveform` – uložení;
- `submit` s `save_data` – export dat;
- `loaddata` – import dat;
- `recalc` – přepočet.

`fm:formparams` nese `form-id`, `creationdate`, `xmlcore-version` a barvy polí.

## 7. Výměna dat

**Uložit data do souboru:** obsah datové instance jako samostatné XML (kořen = kořen `form:instance`).

**Import dat:** přijme XML se stejným kořenovým prvkem a hodnoty vloží jako data formuláře. Formulář se tím nemění.

## 8. Ověření podpisu

Pro každou vrstvu se kontroluje:
1. digest referencovaného `Object` (c14n podle transformace);
2. podpis nad `SignedInfo`;
3. certifikát z `KeyInfo` a jeho důvěryhodnost.

Důvěryhodnost se hodnotí v úložišti certifikátů systému: na Macu přes `SecTrustEvaluateWithError`, ve Form Filleru přes úložiště Windows. Form Filler hlásí vydavatele Software602 jako „nebyl nalezen certifikát vystavitele“, pokud v systému chybí jejich CA. Nejde o chybu podpisu.

Implementace: `zfo_verify.py` (ověření), `zfo_sign.py` (podpis), `macsign/` (klíč v klíčence nebo tokenu).

## Pomocné programy

```text
zfo_sign.py IN.zfo OUT.zfo KEY.pem CERT.pem [--sha1] [pole=hodnota ...]
zfo_verify.py FORM.fo|FORM.zfo
macsign list [--keychain CESTA]
macsign sign --cert SHA1HEX [--alg sha256|sha1] [--keychain CESTA] < data > podpis.b64
macsign trust < certifikáty DER v base64, po jednom na řádku
winsign.ps1 list
winsign.ps1 sign SHA1 sha1|sha256 SOUBOR
winsign.ps1 trust < certifikáty DER v base64, po jednom na řádku
```

`macsign` podepisuje klíčem z klíčenky macOS nebo tokenu přes CryptoTokenKit, `winsign.ps1` je jeho protějšek pro úložiště
certifikátů Windows (`Cert:\CurrentUser\My`, včetně čipových karet) a funguje ve Windows PowerShellu 5.1 i PowerShellu 7.
Klíč systém neopustí a token si o PIN řekne systémovým dialogem. `list` vrací JSON s poli `sha1`, `subject`, `token` a
`certificate`, `trust` vypíše na každý řádek jeden JSON s poli `trusted` a `reason`.
