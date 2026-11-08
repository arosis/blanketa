"""What every test file shares: the project on the import path, no downloads, and helpers several of them use.

    .venv/bin/python -I -m unittest discover -s tests                     # all tests
    .venv/bin/python -I -m unittest discover -s tests -p test_pdf.py      # one file
"""
import importlib.util
import json
import os
import re
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


def page_data(page):
    return json.loads(re.search(r'<script type="application/json" data-zfo-page[^>]*>(.*?)</script>', page, re.S).group(1))


def ui_source(name):
    return (ROOT / "ui" / name).read_text(encoding="utf-8")


# Runs whole page scripts the way a page loads them, on a window without a host and a document that draws nothing.
UI_SCRIPTS_IN_NODE = """
const vm = require('node:vm'), fs = require('node:fs');
const element = () => ({style: {}, append() {}, addEventListener() {}});
const document = {addEventListener() {}, querySelectorAll: () => [], createElement: element, head: element(), body: element()};
const context = vm.createContext({console, setTimeout, process, navigator: {platform: 'Linux'}, document});
context.window = context;
for (const path of %s) vm.runInContext(fs.readFileSync(path, 'utf8'), context, {filename: path});
vm.runInContext(%s, context);
"""


def run_ui_scripts(names, script, *args):
    paths = [str(ROOT / "ui" / name) for name in names]
    return run_in_node(UI_SCRIPTS_IN_NODE % (json.dumps(paths), json.dumps(script)), *args)
