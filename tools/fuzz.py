"""Feeds damaged forms, PDF files and certificates to the code that reads them; any unexpected exception is a bug.

    python -I tools/fuzz.py <target> [seconds] [libFuzzer options]
    python -I tools/fuzz.py --replay <target> <file>
    python -I tools/fuzz.py --check <target>

Atheris guides the search where it is installed (Linux in CI, see .github/workflows/fuzz.yml). Without it the target
runs on random mutations of the same seeds, which is enough to try a target locally. Exit status 1 means a finding.
"""
import base64
import hashlib
import os
import random
import signal
import sys
import tempfile
import time
import traceback
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # never a download, whatever the input says
warnings.simplefilter("ignore")

from cryptography import x509  # noqa: E402
from lxml import etree  # noqa: E402

import app  # noqa: E402
import pdf_sign  # noqa: E402
import revocation  # noqa: E402
import trustlist  # noqa: E402
import zfo_verify  # noqa: E402
from zfoengine import js602  # noqa: E402

FOLDER = Path(tempfile.mkdtemp(prefix="blanketa-fuzz-"))
NOW = datetime.now(timezone.utc)
TIME_LIMIT = 30  # seconds for one input: longer means a hostile file can hang the window
DSIG = f"{{{zfo_verify.DS}}}"


def fo_seeds():
    return [zipfile.ZipFile(p).read("form.fo") for p in sorted((ROOT / "samples").glob("*/form.zfo"))]


def pdf_seeds():
    return [p.read_bytes() for p in sorted((ROOT / "samples-pdf").rglob("*.pdf"))]


def certificate_seeds():
    found = set()
    for fo in fo_seeds():
        for element in etree.fromstring(fo, etree.XMLParser(huge_tree=True)).iter(f"{DSIG}X509Certificate"):
            found.add(base64.b64decode(element.text.replace("\n", "")))
    return sorted(found)


def is_form(data):
    """Whether the input is XML at all; text that is not one is simply refused when opening."""
    try:
        return etree.fromstring(data, etree.XMLParser(huge_tree=True))
    except (etree.XMLSyntaxError, ValueError):
        return None


def fuzz_zfo_signatures(data):
    """The signature panel of any form that parses."""
    root = is_form(data)
    if root is not None:
        try:
            zfo_verify.describe_signature_layers(root)
        except ValueError as error:  # the one way a form is refused: no readable certificate
            if "readable certificate" not in str(error):
                raise


def fuzz_zfo_description(data):
    """The form description and scripts notice of any form that parses."""
    root = is_form(data)
    if root is not None:
        app.binds_with_unsupported_scripts(root)
        app.page_geometry(root)


def fuzz_script(data):
    """A form's script: it either finishes or says why not, within its budget, whatever it is made of."""
    text = data.decode("utf-8", "replace")
    try:
        js602.evaluate(js602.Interpreter([text]), "{" + text + "}", lambda expression: "1", "/d:root/d:x", lambda *args: "")
    except (js602.Unsupported, js602.Exhausted, js602.Thrown):
        pass


def script_seeds():
    """The scripts of the forms in samples/, which are published anyway, and of the form written for the tests."""
    found = set()
    paths = [*(ROOT / "samples").glob("*/form.zfo"), ROOT / "samples/skripty/vypocty.zfo"]
    for path in sorted(paths):
        root = etree.fromstring(zipfile.ZipFile(path).read("form.fo"), etree.XMLParser(huge_tree=True))
        found.update(element.text.encode() for element in root.iter("{http://software602.cz/forms}script") if element.text)
        found.update(value.encode() for bind in root.iter("{http://software602.cz/forms}bind") for value in bind.attrib.values()
                     if js602.is_script(value))
    return sorted(found)


def fuzz_pdf(data):
    """What opening a PDF reads. A damaged file may be refused, the server then answers with a message and libraries
    fail in many ways doing so. The finding here is a hang or running out of memory or stack."""
    for read in (pdf_sign.describe_signatures, pdf_sign.form_fields, app.pdf_info):
        try:
            read(data)
        except (TimeoutError, MemoryError, RecursionError):
            raise
        except Exception:  # noqa: BLE001, S112
            continue


def fuzz_certificate(data):
    """The trust and revocation verdicts: they promise an answer for any certificate a document carries."""
    trustlist.check_certificate(data, FOLDER)
    revocation.check_certificate_revocation(data, FOLDER, FOLDER)


def fuzz_trust_list(data):
    """A downloaded trusted list: refused unless authentic, without any other failure than the documented ones."""
    try:
        root = trustlist._parse_list_xml(data)
        trustlist.verify_list_signature(root, trustlist.PINNED_SIGNERS)
    except (ValueError, KeyError, TypeError, AttributeError, trustlist.InvalidSignature, trustlist.UnsupportedAlgorithm,
            x509.InvalidVersion, etree.LxmlError):
        return
    raise AssertionError("an unsigned or altered list was accepted")


def fuzz_revocation_list(data):
    """A downloaded revocation list: refused unless the authority signed it, and these inputs start from another key."""
    if revocation._parse_usable_list(data, test_revocation().AUTHORITY, NOW) is not None:
        raise AssertionError("a list signed by someone else was accepted")


def test_revocation():
    """Test helpers build the seeds that must look real; imported late, they generate keys."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_revocation as helpers
    return helpers


def trust_list_seeds():
    sys.path.insert(0, str(ROOT / "tests"))
    import test_trustlist as helpers
    return [helpers.national([helpers.service(helpers.AUTHORITY)], helpers.CZ_SIGNER)]


def revocation_list_seeds():
    helpers = test_revocation()
    return [helpers.crl(key=helpers.OTHER_KEY)]


TARGETS = {
    "zfo-signatures": (fuzz_zfo_signatures, fo_seeds),
    "zfo-description": (fuzz_zfo_description, fo_seeds),
    "pdf": (fuzz_pdf, pdf_seeds),
    "script": (fuzz_script, script_seeds),
    "certificate": (fuzz_certificate, certificate_seeds),
    "trust-list": (fuzz_trust_list, trust_list_seeds),
    "revocation-list": (fuzz_revocation_list, revocation_list_seeds),
}


def _too_slow(*_):
    raise TimeoutError(f"one input took over {TIME_LIMIT} s")


def mutate(data, rng):
    """One of the changes that break parsers: flipped bytes, removed or repeated pieces, extreme values."""
    data = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        if not data:
            break
        at, kind = rng.randrange(len(data)), rng.randrange(5)
        size = min(rng.choice((1, 2, 4, 16, 256, 4096)), len(data) - at)
        if kind == 0:
            data[at] ^= 1 << rng.randrange(8)
        elif kind == 1:
            del data[at:at + size]
        elif kind == 2:
            data[at:at] = data[at:at + size] * rng.choice((1, 2, 16))
        elif kind == 3:
            data[at:at + size] = bytes(rng.choice((0, 0xff, 0x7f, 0x80)) for _ in range(size))
        else:
            other = rng.randrange(len(data))
            data[at:at + size] = data[other:other + size]
    return bytes(data)


def report(name, data, error):
    """Keeps the input for replaying and describes the place, so a repeated failure can be recognised."""
    digest = hashlib.sha256(data).hexdigest()[:16]
    out = ROOT / "fuzz-findings" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{digest}.bin").write_bytes(data)
    last = traceback.extract_tb(error.__traceback__)[-1]
    print(f"FINDING {name} {type(error).__name__} {Path(last.filename).name}:{last.lineno} {digest}: {str(error)[:160]}", flush=True)
    return f"{type(error).__name__}@{Path(last.filename).name}:{last.lineno}"


def run_random(name, target, seeds, seconds):
    """Keeps going after a finding and reports each distinct place once, so one run shows everything it found."""
    rng, deadline, runs, seen = random.Random(int(os.environ.get("FUZZ_SEED", "1"))), time.monotonic() + seconds, 0, set()
    signal.signal(signal.SIGALRM, _too_slow)
    while time.monotonic() < deadline:
        data = mutate(rng.choice(seeds), rng)
        runs += 1
        signal.alarm(TIME_LIMIT)
        try:
            target(data)
        except Exception as error:  # noqa: BLE001 - this is what the fuzzer looks for
            place = f"{type(error).__name__}@{Path(traceback.extract_tb(error.__traceback__)[-1].filename).name}:{traceback.extract_tb(error.__traceback__)[-1].lineno}"
            if place not in seen:
                seen.add(place)
                report(name, data, error)
        finally:
            signal.alarm(0)
    print(f"{name}: {runs} inputs, {len(seen)} distinct findings", flush=True)
    return 1 if seen else 0


def run_atheris(atheris, target, seeds, seconds, options):
    corpus = FOLDER / "corpus"
    corpus.mkdir()
    for index, seed in enumerate(seeds):
        (corpus / str(index)).write_bytes(seed)
    atheris.instrument_all()
    atheris.Setup([sys.argv[0], str(corpus), f"-max_total_time={seconds}", f"-timeout={TIME_LIMIT}", "-rss_limit_mb=2048",
                   "-max_len=4000000", "-artifact_prefix=fuzz-findings/", *options], target)
    (ROOT / "fuzz-findings").mkdir(exist_ok=True)
    atheris.Fuzz()


def main(arguments):
    if arguments[:1] == ["--check"]:  # the target runs on its unchanged seeds: nothing random, so it cannot fail by chance
        target, seeds = TARGETS[arguments[1]]
        for seed in seeds():
            target(seed)
        return 0
    if arguments[:1] == ["--replay"]:
        TARGETS[arguments[1]][0](Path(arguments[2]).read_bytes())
        print("no failure")
        return 0
    name, seconds, options = arguments[0], int(arguments[1]) if len(arguments) > 1 else 60, arguments[2:]
    target, seeds = TARGETS[name]
    try:
        import atheris
    except ImportError:
        return run_random(name, target, seeds(), seconds)
    return run_atheris(atheris, target, seeds(), seconds, options)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
