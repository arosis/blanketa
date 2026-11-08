# Ověřování kontejnerů ASiC a podpisů XAdES – návrh

Karta: [arosis/blanketa#63](https://github.com/arosis/blanketa/issues/63)

## Proč

Úřady v EU musí přijímat podpisy XAdES, CAdES, PAdES a kontejnery ASiC-E (.asice, .sce) a ASiC-S (.asics, .scs) (prováděcí rozhodnutí 2015/1506, nově 2026/248). Kontejnery dnes vytváří například Software602 Signer (.asics pro jeden soubor, .asice pro více), EU DSS, estonský DigiDoc a slovenský slovensko.sk, který na ASiC-E s XAdES přešel. Kdo takový soubor dostane, potřebuje zjistit, co je uvnitř, kdo co podepsal a jestli se od podpisu nic nezměnilo. Blanketa už podpisy ZFO a PDF ověřuje, takže to lidé čekají i u kontejnerů.

## Cíle

- Otevřít kontejner ASiC-S nebo ASiC-E s podpisy XAdES a ukázat seznam souborů, které obsahuje.
- U každého podpisu ukázat podepisujícího, čas, jestli je podpis neporušený a jestli je certifikát důvěryhodný podle seznamů EU. Postup i texty jsou stejné jako u PDF a ZFO, včetně kontroly odvolání.
- U každého souboru ukázat stav: „podepsáno“, „změněno po podpisu“ nebo „nepodepsáno“ (soubor nepokrývá žádný podpis).
- Poškozený nebo úmyslně změněný kontejner nebo soubor se ukáže jako neplatný, nikdy jako platný.
- Soubory z kontejneru jde uložit přes dialog. PDF a ZFO jde také otevřít v novém okně Blankety.
- Samostatný soubor XAdES (.xml), který podepsaná data nese v sobě (enveloping, enveloped), se ověří stejně.

## Mimo rozsah (každé jako samostatná karta)

- Podpisy CAdES uvnitř ASiC (`META-INF/signature.p7s`). Takový kontejner ukáže soubory a náhled s poznámkou „Tento typ podpisu zatím neumíme ověřit“.
- Samostatný XAdES s odděleným podpisem (detached), kde data leží v jiném souboru.
- Vytváření podpisů a kontejnerů. Majitel dřívější úkol s podepisováním odložil, protože „moc narostl“. Tady jde jen o ověřování.
- Časová razítka a záznamy důkazů ASiC bez podpisu (`timestamp.tst`, `evidencerecord.*`). Kontejner ukáže soubory s poznámkou, že tento typ zatím neumíme ověřit.
- Náhledy jiných souborů než PDF (obrázky, text, Office).

## Čeho se vyvarovat

- Ukázat podpis jako platný, když platný není. Chybějící nebo nepochopená věc vede vždy k „neplatný“ nebo „nelze ověřit“, nikdy k „platný“.
- Spustit soubor z kontejneru nebo ho otevřít ve výchozí aplikaci systému.
- Pustit obsah souboru do stránky jinak než jako data JSON nebo textContent.
- Nafouknout změnu do podepisování, úprav ZFO nebo PDF nebo velkého přepisu sdíleného kódu. Na zfo_verify.py a trustlist.py teď souběžně pracuje sezení pro zprávy ISDS (#60), proto jsou změny v nich co nejmenší a oddělené.

## Formáty (shrnutí norem)

Normy: ETSI EN 319 162-1 V1.1.1 (ASiC), EN 319 132-1 V1.3.1 (XAdES), EN 319 102-1 (postup ověření).

- **Kontejner** je ZIP s názvy v UTF-8 a kompresí stored nebo deflate. Soubor `mimetype` je nepovinný. Když je v kontejneru, musí být první, nekomprimovaný a bez extra polí. Obsahuje `application/vnd.etsi.asic-s+zip` nebo `application/vnd.etsi.asic-e+zip`.
- **ASiC-S:** v kořeni je právě jeden datový soubor a v `META-INF/signatures.xml` jsou podpisy. Kořenový prvek je `asic:XAdESSignatures` a obsahuje jeden nebo více odděleně uložených prvků `ds:Signature`. Každý podpis se vztahuje na ten jeden datový soubor.
- **ASiC-E:** v `META-INF/*signatures*.xml` je jeden nebo více souborů a každý obsahuje jeden nebo více podpisů. Podpis odkazuje na soubory přes `ds:Reference` s relativní URI vůči kořeni kontejneru (`smlouva.pdf` i `/smlouva.pdf`) nebo přes podepsaný `ds:Manifest`. Každý podpis může pokrývat jiné soubory. Kořenový prvek může být `asic:XAdESSignatures`, `document-signatures` (ODF), `signatures` (OCF) nebo jiný obal sourozenců `ds:Signature`. V `META-INF/manifest.xml` je seznam souborů.
- **Rozpoznání:** podle obsahu, ne podle přípony. Kontejner je ZIP s `META-INF/*signatures*.xml` nebo `META-INF/signature.p7s`. ASiC-S od ASiC-E se pozná podle `mimetype`, a když chybí, podle počtu datových souborů a názvu `signatures.xml`.
- **XAdES:** `QualifyingProperties` (jmenné prostory `http://uri.etsi.org/01903/v1.3.2#` a `v1.4.1#`) v jediném `ds:Object`. `Target` ukazuje na Id podpisu. `SignedProperties` je podepsaná referencí s typem `http://uri.etsi.org/01903#SignedProperties`. V nich je `SigningTime`, tedy čas, který uvádí podepisující, a `SigningCertificateV2` (u starších podpisů `SigningCertificate`), jehož první `CertDigest` je otisk certifikátu podepisujícího v DER. V `UnsignedProperties/UnsignedSignatureProperties/SignatureTimeStamp` je token RFC 3161. Jeho otisk se počítá z prvku `ds:SignatureValue` kanonizovaného metodou uvedenou v razítku (výchozí je C14N 1.0).

## Ověření jednoho podpisu

Podpis je **neporušený** jen tehdy, když platí všechno níže. Jinak je **porušený** a karta uvede první nalezený důvod česky.

1. `SignedInfo` používá kanonizaci C14N 1.0 nebo exkluzivní C14N (s komentáři i bez). Algoritmus podpisu je RSA PKCS#1 v1.5 se SHA-256, SHA-384 nebo SHA-512, nebo ECDSA se SHA-256, SHA-384 nebo SHA-512. Cokoli jiného je porušený podpis s důvodem „neznámý algoritmus“. RSA-SHA1 se nepřijímá.
2. Každá `ds:Reference`:
   - Algoritmus otisku je SHA-256, SHA-384 nebo SHA-512. SHA-1 se přijme jen u otisků podepsaných souborů a `SignedProperties`, aby šly ověřit starší podpisy. MD5 nikdy.
   - Povolené transformace jsou jen kanonizace (C14N 1.0 a 1.1 a exkluzivní, s komentáři i bez) a enveloped-signature. Jiná transformace (XPath, XSLT, base64…) znamená porušený podpis.
   - URI `#id` se dohledá jen jako jediný prvek s tímto Id uvnitř téhož `ds:Signature`, tedy `SignedProperties`, `ds:Object` nebo `ds:Manifest`. Duplicitní Id kdekoli v souboru podpisu znamená porušený podpis. Tím se brání zabalení podpisu (signature wrapping), stejně jako `signed_object` v zfo_verify.py.
   - Jiná URI je soubor v kontejneru. Dekóduje se procenty, odstraní se úvodní `/` a musí přesně odpovídat jménu jednoho datového souboru mimo `META-INF/`. Absolutní URI (`http:`, `file:`…), `..`, zpětné lomítko a odkaz do `META-INF/` znamenají porušený podpis. Chybějící soubor také.
   - Reference bez URI je povolená jen v ASiC-S a znamená ten jeden datový soubor.
   - U samostatného XAdES je povolená jen URI `#id` (enveloping) a prázdná URI s transformací enveloped-signature (enveloped, celý dokument).
3. Musí existovat právě jedna reference na `SignedProperties` se správným typem a ukazovat na prvek uvnitř `QualifyingProperties`, jehož `Target` je `#` + Id tohoto podpisu.
4. `ds:Manifest` se ověří jen tehdy, když je sám podepsaný referencí. Pravidla pro jeho reference jsou stejná jako v bodě 2.
5. Certifikát podepisujícího je první `X509Certificate` v `KeyInfo`, kterému sedí otisk v `SigningCertificateV2`, případně v `SigningCertificate`. Když `SigningCertificate*` chybí nebo otisk nesedí s žádným certifikátem v `KeyInfo`, podpis je porušený.
6. `SignatureValue` sedí s veřejným klíčem tohoto certifikátu.

Výjimky z bodu 2:

- Reference přesně na `META-INF/manifest.xml` je povolená, protože ji běžně podepisují ODF, OCF i někteří výrobci ASiC-E. Ostatní cesty do `META-INF/` zůstávají zakázané.
- Transformace base64 (`http://www.w3.org/2000/09/xmldsig#base64`) je povolená jen u samostatného enveloping XAdES, jako jediná transformace reference na `ds:Object`. Takto tam binární data ukládá například EU DSS. V seznamu souborů se pak ukáže dekódovaný obsah pod jménem „podepsaný obsah“ s příponou podle `MimeType` objektu, jinak `.bin`. Jde náhledem zobrazit (je-li to PDF) a uložit jako soubor z kontejneru.
- SHA-1 se přijímá i v `CertDigest` v `SigningCertificate` a `SigningCertificateV2`, protože starší podpisy ho tam mají skoro vždy.

**Stav souboru** (v tomto pořadí):

1. „změněno po podpisu“: v některém podpisu u něj nesedí otisk.
2. „podepsáno“: pokrývá ho aspoň jeden neporušený podpis.
3. „podpis neplatný“: odkazují na něj jen porušené podpisy (otisk sedí, ale podpis je neplatný z jiného důvodu, nebo reference používá nepovolený algoritmus či transformaci).
4. „neověřeno“: kontejner nese podpis nebo razítko typu, který zatím neumíme ověřit (CAdES, `timestamp.tst`, `evidencerecord.*`).
5. „nepodepsáno“: neodkazuje na něj žádný podpis.

Soubory v `META-INF/` se v seznamu neukazují.

**Důvod porušení:** popis podpisu dostane navíc pole `reason`. Když je podpis porušený kvůli nesedícímu otisku souboru nebo `SignedProperties`, karta ukáže dnešní text „Podpis je porušen – podepsaný obsah byl změněn“ (u souboru i s jeho jménem). Když je porušený z jiného důvodu, ukáže „Podpis je neplatný – <důvod>“, např. „neznámý algoritmus podpisu“, „certifikát podepisujícího nesouhlasí s podepsaným otiskem“, „nepodporovaná kanonizace“. Karty PDF a ZFO se nemění; pole `reason` je nepovinné a zobrazí se jen tam, kde je vyplněné.

**Časová razítka:** každý `SignatureTimeStamp` se ověří jako razítko v PDF (neporušenost tokenu, sedící otisk nad kanonizovaným `SignatureValue`, kvalifikace TSA přes `trustlist.check_stamp_certificate`) a do karty jde jako `stamps`. Data z úrovní B-LT a B-LTA (`CertificateValues`, `RevocationValues`, archivní razítka) se ignorují. Úroveň B-LTA se tedy ověří jako B-T a archivní razítka se neukazují. Odvolání se zjišťuje vlastní cestou Blankety (CRL vydavatelů ze seznamů EU, `revocation.py`).

**Důvěryhodnost** se zjistí stejně jako u PDF, přes `app.add_trust_verdicts`. Proto má popis podpisu stejný tvar jako u PDF: `subject, issuer, publisher, intact, modified, changes_unchecked, not_after, signed_at, der, stamps` a navíc `files` se seznamem jmen, která podpis pokrývá.

**Kontejner jako celek** je poškozený, když ZIP nejde přečíst, má šifrované položky, nepovolenou kompresi, duplicitní jména, jména s `..` nebo absolutní cestou, nebo překročí limity: nejvýš 1 000 položek, nejvýš 100 MB rozbalených dat a poměr komprese nejvýš 100 : 1 na položku. Poškozený kontejner skončí stránkou s chybou jako dnešní poškozený soubor (DAMAGED_PAGE). Porušení pravidel ASiC, která nemají vliv na podpis (chybějící `manifest.xml`, `mimetype` není první položka, ASiC-S má víc datových souborů), se ukáže jako upozornění v panelu „Kontejner“ a neznamená porušený podpis.

## Parsování XML

- Pro soubory podpisů je jeden zpevněný parser lxml: `resolve_entities=False`, `no_network=True`, `load_dtd=False`, `huge_tree=False` a odmítnutí dokumentu, který obsahuje DOCTYPE. Pokrývá tak XXE i rozbalování entit („billion laughs“).
- Parser zfo_verify.py zůstává beze změny (ZFO používá `huge_tree=True` kvůli velkým přílohám).
- Kanonizace se dělá přes `etree.tostring(method="c14n", exclusive=…, with_comments=…)`. C14N 1.1 přes lxml není, proto se reference s C14N 1.1 ověří jen tam, kde se výsledek shoduje s C14N 1.0 (bez `xml:id` a `xml:base`). Jinak je podpis porušený s důvodem „nepodporovaná kanonizace“.

## Okno kontejneru (varianta B, schválená)

Rozložení jako u PDF a ZFO: lišta nahoře, uprostřed náhled, vpravo panel a dole stavový řádek.

- **Uprostřed** je náhled vybraného souboru. PDF se zobrazí přes stejné PDF.js, jaké dnes používá `ui/pdf.html`, ale jen pro čtení: stránky jde listovat a přibližovat, formuláře jsou zakázané (`annotationMode` jen zobrazuje). Ostatní soubory ukážou ikonu, název, velikost, stav a tlačítko „Uložit soubor…“. Při otevření se vybere první PDF, a když žádné není, první soubor.
- **Vpravo nahoře** je panel „Kontejner“: typ (např. „ASiC-E s podpisy XAdES“), počet souborů, z toho kolik je nepodepsaných, a případná upozornění.
- **Pod ním** je seznam „Soubory v kontejneru“ se stavy podepsáno / změněno po podpisu / nepodepsáno. Klepnutí změní náhled.
- **Pod ním** jsou „Elektronické podpisy“: karty ve stejném stylu a se stejnými texty jako v `ui/pdf.html` (důvěryhodnost, razítko, odvolání) a navíc řádek „Podepisuje:“ se jmény souborů.
- **Lišta** má tlačítka „Uložit soubor…“ a „Otevřít v Blanketě“. Druhé je aktivní jen u PDF a ZFO.
- **Stavový řádek** ukazuje název kontejneru a souhrn, např. „Jeden podpis je porušený.“
- **Samostatný XAdES** používá stejné okno. Seznam souborů obsahuje podepsaný obsah, nebo nic, když jde o celý dokument (enveloped).

Návrh obrazovky je ve skice z brainstormingu, varianta B. Kód stránky, nejspíš nový `ui/container.html`, si přebírá CSS z `ui/shell.html`. Jestli se karty podpisů vytáhnou z `ui/pdf.html` do sdíleného souboru, nebo se zkopírují, rozhodne stavitel. Rozhodující je, aby texty zůstaly shodné.

Všechno, co pochází ze souboru (jména souborů, jména podepisujících, důvody), jde do stránky přes `app.json_for_script()` a vykresluje se přes `textContent` nebo `escapeHtml()`.

## Server a hostitelé

- Nová trasa GET `/container-file?s=<session>&i=<index>` vrátí obsah položky kontejneru podle indexu v seznamu, nikdy podle jména z požadavku. Pro PDF vrací `application/pdf`, jinak `application/octet-stream` s `Content-Disposition: attachment`. Data se čtou z kontejneru načteného v sezení v paměti.
- **Uložit soubor…** neposílá obsah přes most stránky (u souboru o desítkách MB by to bylo pomalé a křehké). Stránka pošle novou zprávu `zfoNative` `saveContainerFile` s adresou `/container-file?s=…&i=…` a navrženým jménem. Hostitel ukáže dialog uložení se složkou kontejneru, sám si obsah stáhne z lokálního serveru (127.0.0.1, stejný port) a zapíše ho binárně. Doplní se do všech tří hostitelů: `ui/bridge.js` (web: obyčejné stažení odkazem), `macapp/main.swift` (URLSession) a `desktop.py` (urllib). Navržené jméno je jen poslední část cesty uvnitř kontejneru, očištěná od oddělovačů.
- **Otevřít v Blanketě** (rozhodnutí uživatele: nejdřív uložit kopii) ukáže stejný dialog uložení jako „Uložit soubor…“ (navržené očištěné jméno, složka kontejneru), zapíše soubor a uloženou kopii otevře v novém okně existující cestou `openFile`. Kopie je obyčejný soubor na místě, které uživatel zvolil, takže vyplněné a podepsané verze vzniknou vedle ní a do nedávných souborů se dostane skutečná cesta. Dočasná složka se nepoužívá. Když uživatel dialog zruší, nic se neotevře. Nic se nespouští a nic se neotevírá výchozí aplikací systému.
- Server dál poslouchá jen na 127.0.0.1 a POST trasy vyžadují token sezení.
- `_serve_form_page` pozná kontejner nebo XAdES podle obsahu a zavolá nové `render_container_page`. Chyba čtení vede na DAMAGED_PAGE jako dnes.

## Registrace typů souborů a dialog Otevřít

Blanketa se zaregistruje pro .asice, .sce, .asics a .scs stejně jako dnes pro .zfo:

- `macapp/Info.plist`: nové UTI (`application/vnd.etsi.asic-e+zip` a `asic-s+zip`) s rolí Viewer a LSHandlerRank Owner.
- `packaging/windows/blanketa.iss`: „Otevřít v programu“ vždy. Jako výchozí program jen přes volitelnou úlohu instalátoru „Otevírat kontejnery ASiC (.asice, .asics) v Blanketě“, obdobně jako `zfodefault`.
- `packaging/linux/blanketa.desktop` a `blanketa-mime.xml`: typy MIME s příponami.
- `OPEN_DIALOG_FILE_TYPES` v desktop.py a `NSOpenPanel` v main.swift přidají kontejnery ASiC. Samostatný XAdES se ve Windows a Linuxu otevírá přes „Všechny soubory“; na macOS, kde panel volbu „všechny soubory“ nemá, se do povolených typů přidá `public.xml`. K příponě .xml se Blanketa v systému neregistruje.

Dnešní stav pro srovnání: .zfo a .fo má Blanketa jako vlastník na macOS, na Windows jako volitelný výchozí program a na Linuxu přes MIME. PDF je jen jako alternativa a v nabídce „Otevřít v programu“.

## Kód

- Nový modul, např. `asic_verify.py`: čtení a kontrola ZIP, rozpoznání typu, ověření XAdES a výsledek pro stránku. Z `zfo_verify.py` se převezmou jen potřebné pomocné funkce (konstanty algoritmů a `canonicalize`). Logika XAdES se do `zfo_verify.py` nepřidává.
- Ověření razítka RFC 3161 přes stejnou knihovnu jako u PDF (pyHanko / asn1crypto, `ValidationContext(allow_fetching=False)`), tak aby `judge_time_stamps` fungovalo beze změny.
- Nový modul a stránka patří do `build.sh` i `tools/build_desktop.py`. Nové závislosti se nečekají. Kdyby nějaká byla potřeba, jde do `deps/*.in`, zamkne se `tools/lock-requirements.sh` a dostane řádek v `THIRD-PARTY-NOTICES.md`.

## Testy

- Syntetické kontejnery se podepíší testovací identitou z `pki/` (`tools/make-test-pki.sh`). Nikdy se nepoužijí skutečné dokumenty klientů ani kvalifikované certifikáty. Generátor testovacích kontejnerů žije v testech nebo v `tools/`.
- Hladké případy: ASiC-S s jedním podpisem, ASiC-E se dvěma podpisy na různé soubory, ASiC-E s podepsaným Manifestem, podpis s razítkem (B-T), samostatný enveloping a enveloped XAdES, kontejner s CAdES (jen seznam a poznámka).
- Nepřátelské případy v `tests/test_security.py`:
  - zabalení podpisu (duplicitní Id, `SignedProperties` mimo podpis, druhý `ds:Object`);
  - změněný soubor v kontejneru;
  - změněné `SignedProperties`;
  - neznámý algoritmus otisku, podpisu a transformace;
  - MD5 a RSA-SHA1;
  - XXE, rozbalování entit a DOCTYPE;
  - reference na `../`, `file:`, `http:` a do `META-INF`;
  - podvržený `SigningCertificateV2`;
  - zip bomba, duplicitní jména v ZIP a šifrovaná položka.
- `tools/fuzz.py` dostane cíl `asic` (mutace ZIPu a XML podpisů). Nesmí spadnout a nesmí ukázat porušený vstup jako platný.
- `tools/selftest.py`: kontejner podepsaný testovací identitou se ukáže jako neporušený a po změně souboru jako porušený.
- `tests/test_app.py`: trasa `/container-file` vyžaduje sezení, vrací položku podle indexu a vykreslená stránka nepustí jména souborů do HTML jinak než jako JSON.

## Rozdělení práce

Cíl je jeden PR. Kdyby přerostl rozumnou velikost pro review, rozdělí se na dva:

1. Ověřování (`asic_verify.py`) s testy.
2. Okno, trasy, hostitelé a balíčkování.

Než k rozdělení dojde, stavitel se ozve.

## Co zůstává na staviteli

- Přesné názvy modulů, funkcí a tras.
- Jestli se karty podpisů sdílí s `ui/pdf.html` souborem, nebo kopií.
- Detaily kontrol ZIP (konkrétní limity mohou být jiné, pokud jsou rozumné a otestované).
- Kód generátoru testovacích kontejnerů.

## Rozhodnutí, která udělal Claude (ke kontrole)

- Limity ZIP: 1 000 položek, 100 MB a poměr komprese 100 : 1.
- Porušení pravidel ASiC, která neovlivňují podpis, se ukáže jen jako upozornění, ne jako porušený podpis.
- RSA-SHA1 jako algoritmus podpisu se nepřijímá. SHA-1 se přijímá jen v otiscích referencí.
- Archivní razítka B-LTA a vložená data B-LT se neukazují ani nepoužívají.
- Samostatný XAdES se nezaregistruje k příponě .xml.
- Náhled jen pro PDF, ostatní soubory jen uložit.
- Pátý stav souboru „podpis neplatný“ a stav „neověřeno“ pro typy podpisů, které zatím neumíme.
- Pole `reason` a text „Podpis je neplatný – <důvod>“ u porušení, které není změnou obsahu.
- Povolená reference na `META-INF/manifest.xml`, transformace base64 jen u enveloping XAdES, SHA-1 v `CertDigest`.
- Ukládání souboru z kontejneru: hostitel si obsah stáhne z lokálního serveru sám, nejde přes most stránky.
- Na macOS se do dialogu Otevřít přidá `public.xml`.
