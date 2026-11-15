"""compare_js_engines.py [--image IMAGE] <form or folder>…

Runs every script of the forms in zfoengine/js602.py and in V8 (the engine of Form Filler) and lists where the two differ.
Both get the same answers to what a script asks of the form: once those of the form as it opens, then made-up ones that
lead it through its other branches. V8 runs in a container without network, the scripts come from files nobody vetted.
"""
import argparse
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zfoengine import api as engine  # noqa: E402
from zfoengine import evaluate, js602, ns  # noqa: E402

REPLAY = Path(__file__).with_name("replay_scripts_in_v8.js")
SCRIPT_ATTRIBUTES = ("calculate", "constraint", "relevant", "readonly", "required", "stoprecalc")
ANSWERS_OF_THE_FORM = "form"
MADE_UP_ANSWERS = ("", "0", "1", "2", "-3", "12.5", "100000000", "abc", "true", "false", "2026-01-15", "15.01.2026", "8558150007",
                   "855815/0007", "Žluťoučký kůň", "a,b;c")
SAME_ANSWER_TO_EVERYTHING = {"empty": "", "one": "1"}
MIXED_ANSWERS = ("mixed-1", "mixed-2", "mixed-3", "mixed-4", "mixed-5", "mixed-6")
QUESTIONS_THE_FORM_ITSELF_COULD_NOT_ANSWER = ("DataXpath", "GetListItemData")
MOMENT_BOTH_ENGINES_TAKE_FOR_NOW = js602._make_time(2026.0, 10.0, 15.0, 10.0, 0.0, 0.0)


def forms_in(paths):
    for given in paths:
        for path in sorted(Path(given).rglob("*")) if Path(given).is_dir() else [Path(given)]:
            if path.suffix.lower() in (".zfo", ".fo"):
                yield path


def form_in(path):
    content = path.read_bytes()
    return zipfile.ZipFile(path).read("form.fo") if content[:2] == b"PK" else content


def scripts_of(doc):
    for bind in doc.root.iter("{%s}bind" % ns.FM_URI):
        for attribute in SCRIPT_ATTRIBUTES:
            source = bind.get(attribute) or ""
            if js602.is_script(source):
                yield bind, attribute, source


def made_up_answer(answers, question):
    if answers in SAME_ANSWER_TO_EVERYTHING:
        return SAME_ANSWER_TO_EVERYTHING[answers]
    digest = hashlib.sha256(f"{answers}\0{question}".encode()).digest()
    return MADE_UP_ANSWERS[digest[0] % len(MADE_UP_ANSWERS)]


def as_the_script_gets_it(answer):
    if isinstance(answer, (bool, str)):
        return js602.to_string(answer)
    return "" if answer is None else js602.to_string(float(answer))


def run_in_js602(doc, source, path, answers):
    asked = []

    def remembered(kind, question, answer):
        asked.append([kind, question, answer])
        return answer

    if answers == ANSWERS_OF_THE_FORM:
        of_the_form = evaluate.script_data_query(doc, path=path)
        data = of_the_form
        list_item = evaluate.script_list_item_query(of_the_form)
    else:
        def data(expression):
            return made_up_answer(answers, expression)

        def list_item(*args):
            return made_up_answer(answers, "\0".join(args))

    def birth_number_valid(text):
        return remembered("RCCheck", text, is_birth_number(text))

    is_birth_number, js602._birth_number_valid = js602._birth_number_valid, birth_number_valid
    try:
        result = js602.evaluate(
            doc.script_interpreter(), source,
            lambda expression: remembered("DataXpath", expression, as_the_script_gets_it(data(expression))), path,
            lambda *args: remembered("GetListItemData", "\0".join(args), list_item(*args)))
        outcome = ["nothing"] if result is None else ["result", result]
    except js602.Thrown:
        outcome = ["thrown"]
    except js602.Exhausted:
        outcome = ["exhausted"]
    except js602.Unsupported as unsupported:
        outcome = ["unsupported", str(unsupported)]
    finally:
        js602._birth_number_valid = is_birth_number
    return {"source": source, "path": path, "asked": asked, "outcome": outcome}


def runs_of(path):
    doc = engine.open_document(form_in(path))
    engine.calculate_a_form_nobody_filled_in(doc)
    runs = []
    for bind, attribute, source in scripts_of(doc):
        for answers in (ANSWERS_OF_THE_FORM, *SAME_ANSWER_TO_EVERYTHING, *MIXED_ANSWERS):
            run = run_in_js602(doc, source, doc._get_abs_data_path(bind), answers)
            runs.append({**run, "bind": bind.get("Id"), "attribute": attribute, "answers": answers})
    return {"form": str(path), "library": doc.script_interpreter().library, "runs": runs}


def node_in_a_container_without_network(image):
    def run_node(folder, script, runs):
        return subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--pids-limit", "128", "--memory", "2g", "--user", "65534:65534", "--volume", f"{folder}:/in:ro", image,
             "node", f"/in/{script}", f"/in/{runs}"], capture_output=True, encoding="utf-8")
    return run_node


def replayed_in_v8(form, run_node):
    with tempfile.TemporaryDirectory() as folder:
        Path(folder).chmod(0o755)
        (Path(folder) / "replay.js").write_text(REPLAY.read_text(encoding="utf-8"), encoding="utf-8")
        (Path(folder) / "runs.json").write_text(json.dumps({"now": MOMENT_BOTH_ENGINES_TAKE_FOR_NOW, "form": form}), encoding="utf-8")
        answer = run_node(folder, "replay.js", "runs.json")
    if answer.returncode:
        raise RuntimeError(f"V8 did not replay {form['form']}: {answer.stderr.strip()[-2000:]}")
    return json.loads(answer.stdout)


def compared(of_js602, of_v8):
    if of_js602[0] == "exhausted":
        return "finished by V8 only" if of_v8[0] in ("result", "nothing") else "stopped by the step budget of js602"
    if of_js602[0] == "unsupported" and of_js602[1] in QUESTIONS_THE_FORM_ITSELF_COULD_NOT_ANSWER:
        return "not answered by the form"
    if of_js602[0] == "unsupported":
        return "run by neither" if of_v8[0] == "thrown" else "js602 does not run"
    if of_js602[0] == "thrown":
        return "same" if of_v8[0] == "thrown" else "different"
    return "same" if of_js602 == of_v8 else "different"


def freeze_the_time_of_js602():
    js602._current_local_time = lambda: MOMENT_BOTH_ENGINES_TAKE_FOR_NOW


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default="node:22-alpine", help="container image with Node.js")
    parser.add_argument("forms", nargs="+")
    arguments = parser.parse_args()
    logging.disable(logging.CRITICAL)
    freeze_the_time_of_js602()
    node = node_in_a_container_without_network(arguments.image)

    forms, unreadable = [], []
    counts = Counter()
    differences = {}
    not_run_by_js602 = Counter()
    v8_where_js602_stopped = Counter()
    for path in forms_in(arguments.forms):
        try:
            form = runs_of(path)
            in_v8 = replayed_in_v8(form, node)
        except Exception as error:
            unreadable.append(f"{path}: {error!r}"[:600])
            continue
        forms.append(form)
        for run, of_v8 in zip(form["runs"], in_v8, strict=True):
            verdict = compared(run["outcome"], of_v8)
            counts[verdict] += 1
            if verdict == "js602 does not run":
                not_run_by_js602[f"{run['outcome'][1]}, V8 gives {json.dumps(of_v8, ensure_ascii=False)[:120]}"] += 1
            elif verdict == "stopped by the step budget of js602":
                v8_where_js602_stopped[of_v8[0]] += 1
            elif verdict in ("different", "finished by V8 only"):
                script = hashlib.sha256(run["source"].encode()).hexdigest()[:12]
                differences.setdefault((script, json.dumps(run["outcome"], ensure_ascii=False), json.dumps(of_v8, ensure_ascii=False)), []).append(
                    (form["form"], run["bind"], run["attribute"], run["answers"]))

    print(f"{len(forms)} forms, {sum(len(form['runs']) for form in forms)} runs: " + ", ".join(f"{count} {what}" for what, count in counts.most_common()))
    for line in unreadable:
        print("not compared:", line)
    if v8_where_js602_stopped:
        print("where the step budget stopped js602, V8 ended with: " + ", ".join(f"{count} {how}" for how, count in v8_where_js602_stopped.most_common()))
    for reason, count in not_run_by_js602.most_common():
        print(f"js602 does not run ({count}): {reason}")
    for (script, of_js602, of_v8), where in sorted(differences.items(), key=lambda item: -len(item[1])):
        form, bind, attribute, answers = where[0]
        print(f"\ndifferent in {len(where)} runs, script {script}, first {form} {bind}/@{attribute} with answers {answers}")
        print("  js602:", of_js602)
        print("  V8:   ", of_v8)
    return 1 if differences or not_run_by_js602 or unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
