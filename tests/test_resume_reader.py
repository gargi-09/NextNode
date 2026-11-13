"""The in-browser resume reader (docs/assets/resume-reader.js): sections, dates, redaction.

Runs the real script under Node with a stub window (skipped without Node). File reading
(pdf.js, mammoth) needs a browser and is checked by hand; this pins the text logic.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "docs" / "assets" / "resume-reader.js"
TRY = (ROOT / "docs" / "try.html").read_text(encoding="utf-8")

RESUME = """Priya Sharma
priya.sharma@example.com | +1 (617) 555-0142 | linkedin.com/in/priya-example | https://github.com/priya-example

Summary
ML engineer who ships search and ranking systems.

Work Experience
Senior ML Engineer, Acme AI                 Jan 2022 - Present
- Trained PyTorch ranking models; deployed on Kubernetes with Docker.
ML Engineer, Northwind                     06/2019 – 12/2021
- Built Elasticsearch search, SQL pipelines, Airflow DAGs.
Teaching assistant (part time)             Sep 2020 - May 2021

Education
MS Computer Science, 2017 - 2019

Skills: Python, PyTorch, SQL, Docker, Kubernetes
Interests
Basket weaving, chess
"""

SCRIPT = r"""
const fs = require("fs");
global.window = {};
eval(fs.readFileSync(process.argv[2], "utf8"));
const R = window.NNResume, now = new Date(2026, 10, 1);
const out = {
  full: R.prepare(process.argv[3], { now }),
  plain: R.prepare("Python developer with Docker and SQL experience, no headings at all here.", { now }),
  dates: R.experience("2015 - 2017\nMar 2018 to Sept 2019", now),
  future: R.experience("Jan 2030 - Present", now),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node")
    script = tmp_path_factory.mktemp("reader") / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    proc = subprocess.run([node, str(script), str(READER), RESUME], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_sections_are_found_by_their_headings(run):
    assert run["full"]["sections"] == ["summary", "experience", "education", "skills", "other"]


def test_experience_merges_overlapping_roles_and_counts_present(run):
    full = run["full"]
    assert full["roles"] == 3 and full["span"] == [2019, 2026]
    # Jun 2019 - Dec 2021 and Jan 2022 - Nov 2026 back to back; the TA role overlaps, so not added
    assert full["years"] == 7.5


def test_contact_details_are_removed_and_only_skill_sections_are_sent(run):
    full = run["full"]
    assert full["removed"] == {"emails": 1, "phones": 1, "links": 2}
    sent = full["text"]
    for leaked in ("example.com", "555-0142", "linkedin", "github", "Basket weaving", "Priya Sharma"):
        assert leaked not in sent, leaked
    assert "PyTorch ranking models" in sent and sent.startswith("[Read in the browser: 3 dated role(s)")
    assert "SKILLS\nPython, PyTorch, SQL" in sent                 # the inline "Skills: ..." heading


def test_text_without_headings_is_sent_whole(run):
    plain = run["plain"]
    assert plain["sections"] == [] and plain["roles"] == 0 and "Docker and SQL" in plain["text"]


def test_date_formats_and_impossible_ranges(run):
    assert run["dates"]["roles"] == 2 and run["dates"]["span"] == [2015, 2019]
    assert run["future"]["roles"] == 0                       # a start in the future is not experience


def test_the_page_pins_every_library_and_calls_the_live_api():
    reader = READER.read_text(encoding="utf-8")
    for url in re.findall(r'url: "([^"]+)"', reader):
        assert re.search(r"/\d+\.\d+\.\d+/", url), url                       # exact versions only
    assert reader.count('sri: "sha384-') == 3
    for src in re.findall(r'<script src="(https://[^"]+)"', TRY):
        assert re.search(rf'src="{re.escape(src)}" integrity="sha', TRY), src
    assert 'DEFAULT_API = "https://nextnode-api-' in TRY
    assert "/api/v1/public/analyze" in TRY and "NNMock.request" in TRY      # live first, offline fallback
