import subprocess
import sys
import unittest

from support import ROOT

A_TEST_THAT_NEVER_ENDS = """
import sys
sys.path.insert(0, sys.argv[1])
import support

def test_that_never_ends():
    while True:
        pass

support.end_a_run_longer_than(1)
test_that_never_ends()
"""


class RunLimit(unittest.TestCase):
    def test_a_run_over_its_limit_ends_with_an_error_and_names_the_test_it_stood_in(self):
        run = subprocess.run([sys.executable, "-I", "-c", A_TEST_THAT_NEVER_ENDS, str(ROOT / "tests")],
                             capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 1)
        self.assertIn("test_that_never_ends", run.stderr)
