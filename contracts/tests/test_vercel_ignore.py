"""site/scripts/vercel-ignore.sh decides whether Vercel deploys. It must skip when nothing the
site is built from changed, and build whenever anything it is built from changes (or is unknown).

Run: python -m pytest contracts/tests"""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "site" / "scripts" / "vercel-ignore.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="needs bash and git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def _repo(tmp_path):
    for rel in ("site/app/page.tsx", "contracts/REFERENCE.md", "contracts/org_rules.schema.json",
                "pipelines/ingestion/repo_miner/ARCHITECTURE.md", "pipelines/ingestion/repo_miner/mine_repo.py",
                "README.md"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("one\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _decide(repo, changed=None, base=None):
    if changed:
        (repo / changed).write_text("two\n")
        _git(repo, "commit", "-qam", "change " + changed)
    env = {"PATH": __import__("os").environ["PATH"]}
    if base:
        env["VERCEL_GIT_PREVIOUS_SHA"] = base
    return subprocess.run(["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True).returncode


@pytest.mark.parametrize("changed", ["README.md", "pipelines/ingestion/repo_miner/mine_repo.py",
                                     "contracts/org_rules.schema.json"])
def test_changes_outside_the_site_skip_the_deployment(tmp_path, changed):
    assert _decide(_repo(tmp_path), changed) == 0


@pytest.mark.parametrize("changed", ["site/app/page.tsx", "contracts/REFERENCE.md",
                                     "pipelines/ingestion/repo_miner/ARCHITECTURE.md"])
def test_changes_to_the_site_or_its_doc_sources_build(tmp_path, changed):
    assert _decide(_repo(tmp_path), changed) == 1


def test_uses_the_last_deployed_commit_when_vercel_gives_one(tmp_path):
    repo = _repo(tmp_path)
    first = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (repo / "site/app/page.tsx").write_text("two\n")
    _git(repo, "commit", "-qam", "site change")
    # the newest commit touches only the README, but the site changed since the last deployment
    assert _decide(repo, "README.md") == 0                     # last commit alone: nothing in the site
    assert _decide(repo, base=first) == 1                      # since the last deployment: the site changed


def test_unknown_base_builds(tmp_path):
    repo = _repo(tmp_path)
    assert _decide(repo, base="0" * 40) == 1                   # not in this clone
    assert _decide(repo) == 1                                  # no parent commit: HEAD^ does not exist


def test_the_script_watches_every_file_build_docs_reads_outside_site():
    sources = re.findall(r'"\.\./([^"]+)":', (ROOT / "site" / "scripts" / "build-docs.mjs").read_text())
    assert sources, "build-docs.mjs SOURCES not found"
    text = SCRIPT.read_text()
    for path in sources:
        assert path in text, f"{path} feeds the site but vercel-ignore.sh does not watch it"
