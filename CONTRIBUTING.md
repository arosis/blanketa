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

## Než otevřete pull request

```bash
.venv/bin/python -I tests/test_engine.py
.venv/bin/python -I tests/test_security.py
.venv/bin/python -I tests/test_app.py
.venv/bin/python -I tools/selftest.py test      # jednou předtím tools/make-test-pki.sh
ruff check .
```

Identifikátory, komentáře a zprávy commitů píšeme anglicky, texty pro uživatele česky s českou typografií. Změna
podpisu musí zachovat platnost dřívějších podpisů. Kód ve `vendor/` neměníme. Přispěním souhlasíte s jeho zveřejněním
pod licencí [GNU GPL 3.0](LICENSE).
