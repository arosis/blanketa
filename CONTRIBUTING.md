# Jak přispět

Díky za zájem o Blanketu! Chyby a nápady hlaste přes [issues](https://github.com/arosis/blanketa/issues), bezpečnostní
chyby soukromě podle [SECURITY.md](SECURITY.md).

## Postup změny

1. Vytvořte větev (nebo fork) a změnu do ní commitněte. Do větve `main` se přímo pushovat nedá.
2. Otevřete pull request do `main`.
3. Pull request musí projít všemi kontrolami CI:
   - **Code quality** – `ruff`, syntaxe JavaScriptu a XSLT, kontrola workflow (`actionlint`);
   - **Tests** – testy a podpisový selftest na macOS, Windows a Linuxu;
   - **Code scanning (CodeQL)** – bez nových bezpečnostních nálezů.
4. Změnu musí schválit vlastník kódu (`.github/CODEOWNERS`). Po schválení se pull request sloučí metodou squash.

Sestavení balíčků a spuštění hotové aplikace (**Build and release**) není povinná kontrola pull requestu. Běží po každém
sloučení do `main`, v pull requestech, které mění balení, okna aplikace nebo závislosti, a vždy před vydáním. Když
selže, vydání nevznikne.

Novou závislost přidejte do `deps/*.in` a spusťte `tools/lock-requirements.sh` (potřebuje `uv`). Soubory
`requirements*.txt` jsou generované a obsahují kontrolní součty, ručně je neupravujte.

## Čitelnost kódu

Kód má být srozumitelný z názvů a ze struktury. Než napíšete komentář, zkuste přejmenovat, rozdělit funkci, pojmenovat
hodnotu nebo napsat test. Když při review něco není jasné, oprava je skoro vždy nový název, ne další věta.

**Názvy**

- Funkce říká, co dělá a s čím: `check_certificate_revocation`, ne `check`. Jméno modulu může být součástí názvu, jen
  když se `modul.funkce` čte jako celá věta (`certstore.config_dir`).
- Hodnoty typu ano/ne začínají `is_`, `has_` nebo `can_`, kolekce jsou v množném čísle.
- Bez zkratek. Výjimkou jsou ustálené pojmy oboru (PDF, ZFO, XML, OCSP, CRL) a jednopísmenná proměnná, která žije
  v jediném výrazu (`for i in range(…)`, souřadnice `x`, `y`).
- Nevysvětlitelná hodnota dostane pojmenovanou konstantu: `SIGNATURE_TIME_STAMP_TOKEN_OID`, ne hodnota s komentářem.
- Složitou podmínku pojmenujte proměnnou nebo funkcí. Dva samostatné `if` jsou lepší než `or`, pokud se tak čtou snáz.

**Struktura**

- Funkce dělá jednu věc. Pevný limit řádků nemáme: funkci rozdělte, když by potřebovala nadpis nebo když se její části
  dají pojmenovat samostatně.
- Oddíly tvoří funkce, třídy a moduly, ne komentáře typu `# --- Oddíl ---`.
- Žádné abstrakce do zásoby a žádný mrtvý kód. Pomocná funkce má smysl, když její název přidá informaci.

**Komentáře a docstringy**

Výchozí stav je žádný komentář. Docstring je komentář a platí pro něj totéž.

- Nepíšeme, *co* kód dělá; to říkají názvy.
- Nepíšeme historii a zdůvodnění rozhodnutí („dřív tu bylo…“). Patří do zprávy commitu a pull requestu.
- Nepíšeme `TODO` ani `FIXME`. Nedokončenou práci zapište jako issue.

Komentář zůstává jen tehdy, když popisuje důvod, který kód vyjádřit neumí, a čtenář bez znalosti historie by bez něj kód
pochopil špatně nebo by ho „opravil“ a něco rozbil. Typicky jde o požadavek specifikace nebo formátu, chování cizí
knihovny či programu nebo obranu proti škodlivému souboru. Důvody, které souvisejí s formátem ZFO, patří spíš do
[docs/zfo-format.md](docs/zfo-format.md).

Bez ohledu na to zůstávají licenční hlavičky, direktivy nástrojů (`noqa`) a popis použití příkazu v nástrojích
v `tools/`.

**Kde pravidla platí**

- Platí pro veškerý nový a měněný vlastní kód ve všech jazycích, včetně testů a `zfoengine/js602.py`.
- `vendor/` neměníme. Převzatý kód v `zfoengine/` kvůli stylu neupravujeme, aby se dal porovnávat s originálem. Pravidla
  platí jen pro řádky, které se mění z jiného důvodu.
- Úprava kvůli čitelnosti nesmí změnit chování a patří do samostatného pull requestu. V podpisovém kódu se důvod daný
  specifikací nebo hrozbou smí z komentáře odstranit, jen když ho dál nese název, test nebo dokumentace.

## Než otevřete pull request

```bash
.venv/bin/python -I tests/test_engine.py
.venv/bin/python -I tests/test_security.py
.venv/bin/python -I tests/test_app.py
.venv/bin/python -I tests/test_trustlist.py
.venv/bin/python -I tests/test_revocation.py
.venv/bin/python -I tools/fuzz.py pdf 60      # damaged files; targets are listed in the script
.venv/bin/python -I tools/selftest.py test      # jednou předtím tools/make-test-pki.sh
ruff check .
```

Identifikátory, komentáře a zprávy commitů píšeme anglicky, texty pro uživatele česky s českou typografií. Změna
podpisu musí zachovat platnost dřívějších podpisů. Kód ve `vendor/` neměníme. Přispěním souhlasíte s jeho zveřejněním
pod licencí [GNU GPL 3.0](LICENSE).
