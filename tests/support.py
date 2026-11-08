"""What every test file shares: the project on the import path, no downloads, and helpers several of them use.

    .venv/bin/python -I -m unittest discover -s tests                     # all tests
    .venv/bin/python -I -m unittest discover -s tests -p test_pdf.py      # one file
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

NS = {"d": "http://software602.cz/sample"}
ISDS_SAMPLES = ROOT / "samples-isds"

needs_node = unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")


def run_in_node(script, *args, env=None):
    return subprocess.run(["node", "-e", script, "--", *args], capture_output=True, encoding="utf-8", check=True, env=env).stdout


def isds_sample_tool():
    if not hasattr(isds_sample_tool, "module"):
        spec = importlib.util.spec_from_file_location("make_isds_samples", ROOT / "tools/make-isds-samples.py")
        isds_sample_tool.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isds_sample_tool.module)
    return isds_sample_tool.module


def isds_sample(name):
    return (ISDS_SAMPLES / f"{name}.zfo").read_bytes()
