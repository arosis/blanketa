"""tools/compare_js_engines.py on the samples of this repository: js602 gives what V8 gives, but for the one known case."""
import sys
import unittest
import zipfile

from support import ROOT, needs_node

sys.path.insert(0, str(ROOT / "tools"))
import compare_js_engines  # noqa: E402

from zfoengine import js602  # noqa: E402

SAMPLES_WITH_SCRIPTS = ("skripty/vypocty.zfo", "cuzk/form.zfo")
SCRIPT_THAT_CALLS_NEW_REGEXP_NEXT_TO_ITS_OWN_REGEXP = "lhuta"


@needs_node
class Js602AgainstV8(unittest.TestCase):
    """Node of this computer replays them: the samples are the project's own, not files nobody vetted."""

    def setUp(self):
        self.addCleanup(setattr, js602, "_current_local_time", js602._current_local_time)
        compare_js_engines.freeze_the_time_of_js602()

    def verdicts(self, sample):
        path = ROOT / "samples" / sample
        form = compare_js_engines.runs_of(path, zipfile.ZipFile(path).read("form.fo"))
        in_v8 = compare_js_engines.replayed_in_v8(form, compare_js_engines.node_of_this_computer)
        return [(run["bind"], compare_js_engines.compared(run["outcome"], of_v8)) for run, of_v8 in zip(form["runs"], in_v8, strict=True)]

    def test_scripts_of_the_samples_give_the_same_in_both(self):
        for sample in SAMPLES_WITH_SCRIPTS:
            with self.subTest(sample):
                verdicts = self.verdicts(sample)
                self.assertTrue(verdicts)
                self.assertEqual({bind for bind, verdict in verdicts if verdict not in ("same", "run by neither")} - {SCRIPT_THAT_CALLS_NEW_REGEXP_NEXT_TO_ITS_OWN_REGEXP}, set())

    def test_new_regexp_next_to_a_regexp_of_the_form_is_the_one_known_difference(self):
        # In V8 the form's own function RegExp replaces the constructor, so the script fails there; js602 keeps the constructor.
        verdicts = self.verdicts("skripty/vypocty.zfo")
        self.assertEqual({verdict for bind, verdict in verdicts if bind == SCRIPT_THAT_CALLS_NEW_REGEXP_NEXT_TO_ITS_OWN_REGEXP}, {"different"})


if __name__ == "__main__":
    unittest.main()
