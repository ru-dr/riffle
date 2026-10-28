"""Download, verification, manifest and closure, with a fake downloader.

Nothing here touches the network. A tiny Parquet file stands in for each table, and
the inventory is built from those files so the checksums and row counts are real.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aidev_ingest import download as download_module
from aidev_ingest import manifest
from aidev_ingest.closure import format_report, pop_closure
from aidev_ingest.download import (
    DiskSpaceError,
    DownloadError,
    ExistingFileMismatch,
    UnsafeOutputDir,
    VerificationError,
    check_disk,
    download_tables,
    ensure_not_committable,
)
from aidev_ingest.pin import TABLES, as_is_tables
from aidev_ingest.verify import expected_tables, sha256_file, verify_file
from helpers import REV, FakeFetcher, write_parquet

_write_parquet = write_parquet


def test_downloads_everything_smallest_first_and_writes_a_manifest(world):
    inventory, files, out = world
    fetcher = FakeFetcher(files)
    outcomes = download_tables(inventory, as_is_tables(), out, fetcher)

    sizes = [expected_tables(inventory)[t].size for t in fetcher.calls]
    assert sizes == sorted(sizes), "smallest file first"
    assert {o.action for o in outcomes} == {"downloaded"}

    rev_dir = out / REV
    for name in as_is_tables():
        assert verify_file(rev_dir / f"{name}.parquet", expected_tables(inventory)[name]) == []
    assert not (rev_dir / ".partial").exists(), "staging folder removed after success"

    loaded = manifest.load(rev_dir)
    assert set(loaded["tables"]) == set(as_is_tables())
    assert {t["state"] for t in loaded["tables"].values()} == {"local_verified"}
    assert loaded["revision"] == REV


def test_a_second_run_skips_everything_and_fetches_nothing(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    second = FakeFetcher(files)
    outcomes = download_tables(inventory, as_is_tables(), out, second)
    assert second.calls == []
    assert {o.action for o in outcomes} == {"skipped"}


def test_a_corrupt_download_is_discarded_and_never_placed(world):
    inventory, files, out = world
    victim = as_is_tables()[3]
    with pytest.raises(VerificationError, match=victim):
        download_tables(inventory, [victim], out, FakeFetcher(files, corrupt={victim}))
    assert not (out / REV / f"{victim}.parquet").exists()
    assert not (out / REV / ".partial" / f"{victim}.parquet").exists()
    assert manifest.load(out / REV) is None, "nothing was verified, so nothing is recorded"


def test_an_existing_file_that_fails_verification_is_never_overwritten(world):
    inventory, files, out = world
    name = as_is_tables()[0]
    rev_dir = out / REV
    rev_dir.mkdir(parents=True)
    bad = rev_dir / f"{name}.parquet"
    bad.write_bytes(b"not the real file")
    fetcher = FakeFetcher(files)

    with pytest.raises(ExistingFileMismatch, match="Not overwriting"):
        download_tables(inventory, [name], out, fetcher)
    assert bad.read_bytes() == b"not the real file"
    assert fetcher.calls == []


def test_an_interrupted_download_leaves_no_final_file_and_a_rerun_completes(world):
    inventory, files, out = world
    name = as_is_tables()[2]
    with pytest.raises(ConnectionError):
        download_tables(inventory, [name], out, FakeFetcher(files, fail_on=name))
    assert not (out / REV / f"{name}.parquet").exists()

    download_tables(inventory, [name], out, FakeFetcher(files))
    assert verify_file(out / REV / f"{name}.parquet", expected_tables(inventory)[name]) == []


def test_not_enough_disk_stops_before_any_download(world, monkeypatch):
    inventory, files, out = world
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(download_module.shutil, "disk_usage", lambda p: usage(10, 10, 5))
    fetcher = FakeFetcher(files)
    with pytest.raises(DiskSpaceError, match="GB free"):
        download_tables(inventory, as_is_tables(), out, fetcher)
    assert fetcher.calls == []


def test_check_disk_passes_when_there_is_room(tmp_path):
    check_disk(tmp_path, 1000)


def test_dry_run_downloads_and_writes_nothing(world):
    inventory, files, out = world
    fetcher = FakeFetcher(files)
    outcomes = download_tables(inventory, as_is_tables(), out, fetcher, dry_run=True)
    assert fetcher.calls == []
    assert not out.exists()
    assert {o.action for o in outcomes} == {"planned"}


def test_only_as_is_tables_are_accepted(world):
    from aidev_ingest.__main__ import DEFAULT_EXPECTED

    inventory, files, out = world
    real = json.loads(DEFAULT_EXPECTED.read_text(encoding="utf-8"))  # has the pruned and skipped tables
    for table in ("pr_commit_details", "all_pull_request"):
        with pytest.raises(DownloadError, match="not `as_is`"):
            download_tables(real, [table], out, FakeFetcher(files))
    with pytest.raises(DownloadError, match="not in the inventory"):
        download_tables(inventory, ["no_such_table"], out, FakeFetcher(files))


@pytest.mark.parametrize("bad_path", ["../escape.parquet", "sub/dir.parquet", "other_name.parquet", "..\\escape.parquet"])
def test_an_inventory_path_that_is_not_the_table_name_is_refused_before_any_write(world, bad_path):
    inventory, files, out = world
    name = as_is_tables()[0]
    tampered = json.loads(json.dumps(inventory))
    next(f for f in tampered["files"] if f["table"] == name)["path"] = bad_path
    fetcher = FakeFetcher(files)
    with pytest.raises(DownloadError, match="refusing"):
        download_tables(tampered, [name], out, fetcher)
    assert fetcher.calls == [] and not out.exists()


def test_a_table_name_that_could_climb_out_of_the_folder_is_refused(world):
    inventory, files, out = world
    tampered = json.loads(json.dumps(inventory))
    entry = tampered["files"][0]
    entry["table"], entry["path"] = "../../x", "../../x.parquet"
    fetcher = FakeFetcher(files)
    # Refused one layer earlier now, when the inventory is read, and still before any write.
    with pytest.raises(ValueError, match="unsafe table name"):
        download_tables(tampered, ["../../x"], out, fetcher)
    assert fetcher.calls == [] and not out.exists()


@pytest.mark.parametrize("bad_revision", ["../..", "main", "b37f9fd", "C" * 40, "c" * 39 + "/", ""])
def test_a_revision_that_is_not_a_commit_hash_is_refused_before_any_write(world, bad_revision):
    inventory, files, out = world
    tampered = {**inventory, "revision": bad_revision}
    fetcher = FakeFetcher(files)
    with pytest.raises(DownloadError, match="40-character commit hash"):
        download_tables(tampered, as_is_tables(), out, fetcher)
    assert fetcher.calls == [] and not out.exists()


def test_a_symlink_in_the_data_folder_is_refused(world, monkeypatch):
    """Windows will not create a symlink without special rights, so the condition is faked:
    what is under test is that a path reported as a symlink stops the run before any write."""
    inventory, files, out = world
    name = as_is_tables()[0]
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == f"{name}.parquet")
    fetcher = FakeFetcher(files)
    with pytest.raises(DownloadError, match="symbolic link"):
        download_tables(inventory, [name], out, fetcher)
    assert fetcher.calls == [] and not out.exists()


def test_a_real_symlink_is_refused_where_the_system_allows_one(world, tmp_path):
    inventory, files, out = world
    name = as_is_tables()[0]
    rev_dir = out / REV
    rev_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.parquet"
    elsewhere.write_bytes(b"x")
    try:
        (rev_dir / f"{name}.parquet").symlink_to(elsewhere)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating symlinks")
    with pytest.raises(DownloadError, match="symbolic link"):
        download_tables(inventory, [name], out, FakeFetcher(files))


def test_an_unsafe_table_name_in_the_inventory_is_rejected(world):
    inventory, _, _ = world
    tampered = json.loads(json.dumps(inventory))
    tampered["files"][0]["table"] = "../evil"
    with pytest.raises(ValueError, match="unsafe table name"):
        expected_tables(tampered)


def test_the_command_line_refuses_an_inventory_for_another_repo_or_revision(tmp_path):
    from aidev_ingest.__main__ import DEFAULT_EXPECTED, main

    good = json.loads(DEFAULT_EXPECTED.read_text(encoding="utf-8"))
    out = tmp_path / "raw"
    for label, change in (("repo", {"repo_id": "someone-else/dataset"}), ("revision", {"revision": "../.."})):
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps({**good, **change}), encoding="utf-8")
        for command in ("verify", "closure", "download"):
            extra = ["--dry-run"] if command == "download" else []
            assert main([command, "--out", str(out), "--expected", str(path), *extra]) == 2, (label, command)
    assert not out.exists()


def test_a_manifest_naming_a_file_outside_the_folder_is_reported_not_followed(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables()[:1], out, FakeFetcher(files))
    rev_dir = out / REV
    loaded = manifest.load(rev_dir)
    loaded["tables"][as_is_tables()[0]]["file"] = "../../etc/passwd"
    manifest.write(rev_dir, loaded)
    problems = manifest.verify_dir(rev_dir, inventory)
    assert any("manifest names the file" in p for p in problems)


def test_partial_runs_accumulate_in_one_manifest(world):
    inventory, files, out = world
    first, second = as_is_tables()[:2]
    download_tables(inventory, [first], out, FakeFetcher(files))
    download_tables(inventory, [second], out, FakeFetcher(files))
    assert set(manifest.load(out / REV)["tables"]) == {first, second}


def test_manifest_has_no_absolute_paths_or_user_details(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    text = (out / REV / manifest.MANIFEST).read_text(encoding="utf-8")
    assert str(out) not in text and str(out.anchor + "Users") not in text
    for entry in json.loads(text)["tables"].values():
        assert not os.path.isabs(entry["file"]) and "/" not in entry["file"] and "\\" not in entry["file"]


def test_manifest_from_a_different_inventory_is_refused(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables()[:1], out, FakeFetcher(files))
    changed = json.loads(json.dumps(inventory))
    changed["files"][0]["size"] += 1
    with pytest.raises(manifest.ManifestError, match="different inventory"):
        manifest.record_table(out / REV, changed, expected_tables(changed)[as_is_tables()[0]])


def test_verify_dir_passes_on_a_good_folder(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    assert manifest.verify_dir(out / REV, inventory) == []


def test_verify_dir_detects_a_flipped_byte(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    target = out / REV / f"{as_is_tables()[4]}.parquet"
    data = bytearray(target.read_bytes())
    data[10] ^= 0x01
    target.write_bytes(bytes(data))
    problems = manifest.verify_dir(out / REV, inventory)
    assert any(as_is_tables()[4] in p and "sha256" in p for p in problems), problems


def test_verify_dir_detects_missing_and_unexpected_files_and_tolerates_deleted_state(world):
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    rev_dir = out / REV
    gone, uploaded = as_is_tables()[0], as_is_tables()[1]
    (rev_dir / f"{gone}.parquet").unlink()
    (rev_dir / f"{uploaded}.parquet").unlink()
    manifest.record_table(rev_dir, inventory, expected_tables(inventory)[uploaded], state="local_deleted")
    _write_parquet(rev_dir / "stray.parquet", 1, "x")

    problems = manifest.verify_dir(rev_dir, inventory)
    assert any(gone in p and "missing" in p for p in problems)
    assert not any(uploaded in p for p in problems), "a local_deleted table is not expected on disk"
    assert any("stray.parquet" in p and "not in the manifest" in p for p in problems)


def test_verify_dir_without_a_manifest(world):
    inventory, _, out = world
    (out / REV).mkdir(parents=True)
    assert "missing" in manifest.verify_dir(out / REV, inventory)[0]


def test_verify_file_reports_size_checksum_rows_and_columns(world, tmp_path):
    inventory, files, _ = world
    name = as_is_tables()[0]
    want = expected_tables(inventory)[name]
    assert verify_file(files[name], want) == []
    assert verify_file(tmp_path / "nope.parquet", want) == ["file is missing"]
    wrong_size = tmp_path / "w.parquet"
    wrong_size.write_bytes(files[name].read_bytes() + b"x")
    assert any("size is" in p for p in verify_file(wrong_size, want))
    # Same size, wrong bytes: the checksum fails, and the bytes are not then parsed as Parquet.
    not_parquet = tmp_path / "n.parquet"
    not_parquet.write_bytes(b"x" * want.size)
    problems = verify_file(not_parquet, want)
    assert any("sha256" in p for p in problems)
    assert not any("Parquet" in p for p in problems), "bytes that failed the checksum are not parsed"

    # Right checksum, wrong claims about the content: rows and columns are then compared.
    wrong_rows = type(want)(want.table, want.path, want.size, want.sha256, want.rows + 1, want.columns)
    assert any("rows, expected" in p for p in verify_file(files[name], wrong_rows))
    wrong_columns = type(want)(want.table, want.path, want.size, want.sha256, want.rows, (("id", "double"),))
    assert any("column names or types" in p for p in verify_file(files[name], wrong_columns))

    # Right checksum but not Parquet at all (the inventory itself would be wrong): reported, not crashed.
    junk = tmp_path / "j.parquet"
    junk.write_bytes(b"not parquet at all")
    claims = type(want)(want.table, want.path, junk.stat().st_size, sha256_file(junk), 1, want.columns)
    assert verify_file(junk, claims) == ["not readable as Parquet (ArrowInvalid)"]


def test_a_missing_checksum_in_the_inventory_is_a_problem(world):
    inventory, files, _ = world
    name = as_is_tables()[0]
    want = expected_tables(inventory)[name]
    no_sum = type(want)(want.table, want.path, want.size, None, want.rows, want.columns)
    assert any("no checksum" in p for p in verify_file(files[name], no_sum))


def _closure_dir(tmp_path: Path, parent_ids, child_ids) -> Path:
    rev_dir = tmp_path / "closure"
    rev_dir.mkdir()
    pq.write_table(pa.table({"id": pa.array(parent_ids, pa.int64())}), rev_dir / "pull_request.parquet")
    pq.write_table(pa.table({"pr_id": pa.array(child_ids, pa.int64())}), rev_dir / "pr_reviews.parquet")
    return rev_dir


def test_closure_counts_rows_pointing_at_unknown_prs_and_nulls(tmp_path):
    rev_dir = _closure_dir(tmp_path, [1, 2, 3], [1, 2, 9, 9, None])
    report = pop_closure(rev_dir)
    entry = report["tables"]["pr_reviews"]
    assert entry["rows"] == 5 and entry["null_pr_id"] == 1 and entry["not_in_pull_request"] == 2
    assert entry["sample_ids"] == [9, 9]
    assert report["tables"]["pr_timeline"] == {"checked": False}
    assert "pr_reviews" in format_report(report)


def test_closure_is_clean_when_every_pr_id_exists(tmp_path):
    report = pop_closure(_closure_dir(tmp_path, [1, 2, 3], [3, 2, 1, 1]))
    assert report["tables"]["pr_reviews"]["not_in_pull_request"] == 0


def test_closure_without_pull_request_is_not_checked(tmp_path):
    report = pop_closure(tmp_path)
    assert report["checked"] is False and "not checked" in format_report(report)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_output_folder_must_be_ignored_when_inside_a_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    with pytest.raises(UnsafeOutputDir):
        ensure_not_committable(repo / "data" / "raw")
    (repo / ".gitignore").write_text("/data/\n", encoding="utf-8")
    ensure_not_committable(repo / "data" / "raw")  # ignored: allowed


def test_a_relative_output_folder_is_judged_from_the_working_directory(tmp_path, monkeypatch):
    """git resolves a relative path against the repo top, not the cwd; the check must not."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("/data/\n", encoding="utf-8")
    monkeypatch.chdir(repo / "sub")

    ensure_not_committable(Path("../data/raw"))  # is repo/data/raw: ignored, allowed
    with pytest.raises(UnsafeOutputDir):
        ensure_not_committable(Path("raw"))  # is repo/sub/raw: not ignored
    with pytest.raises(UnsafeOutputDir):
        ensure_not_committable(Path("-looks-like-an-option"))


class RacingFetcher(FakeFetcher):
    """Creates the final file while the download is in progress, after the existence check."""

    def __init__(self, files: dict[str, Path], final: Path):
        super().__init__(files)
        self.final = final

    def download_file(self, path: str, revision: str, dest_dir: Path) -> Path:
        got = super().download_file(path, revision, dest_dir)
        self.final.write_bytes(b"someone else got here first")
        return got


def test_a_file_that_appears_during_the_download_is_never_overwritten(world):
    inventory, files, out = world
    name = as_is_tables()[0]
    final = out / REV / f"{name}.parquet"
    with pytest.raises(ExistingFileMismatch, match="appeared while"):
        download_tables(inventory, [name], out, RacingFetcher(files, final))
    assert final.read_bytes() == b"someone else got here first"
    manifest_now = manifest.load(out / REV)
    assert manifest_now is None or name not in manifest_now["tables"], "nothing was verified into place"


def test_placement_still_works_without_hard_links_and_still_refuses_to_overwrite(world, monkeypatch):
    inventory, files, out = world

    def no_hard_links(*args, **kwargs):
        raise OSError("this filesystem has no hard links")

    monkeypatch.setattr(download_module.os, "link", no_hard_links)
    first, second = as_is_tables()[:2]
    download_tables(inventory, [first], out, FakeFetcher(files))
    assert verify_file(out / REV / f"{first}.parquet", expected_tables(inventory)[first]) == []

    racing = RacingFetcher(files, out / REV / f"{second}.parquet")
    with pytest.raises(ExistingFileMismatch, match="appeared while"):
        download_tables(inventory, [second], out, racing)


def test_the_manifest_is_written_through_a_unique_temp_file_never_a_guessable_name(world):
    inventory, files, out = world
    rev_dir = out / REV
    rev_dir.mkdir(parents=True)
    planted = rev_dir / ".manifest.json.tmp"
    planted.write_text("planted", encoding="utf-8")
    download_tables(inventory, as_is_tables()[:2], out, FakeFetcher(files))
    assert planted.read_text(encoding="utf-8") == "planted", "the old fixed name is never written to"
    assert [p.name for p in rev_dir.glob(".manifest-*")] == [], "no temp file is left behind"


def test_a_damaged_manifest_is_a_clean_error_not_a_traceback(world, tmp_path):
    from aidev_ingest.__main__ import main
    from aidev_ingest.pin import REPO_ID

    inventory, files, out = world
    inv = {**inventory, "repo_id": REPO_ID}
    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(json.dumps(inv), encoding="utf-8")
    rev_dir = out / REV
    rev_dir.mkdir(parents=True)

    for damaged in (b"\xff\xfe not json", b"[1, 2, 3]", b'{"revision": "x"}'):
        (rev_dir / manifest.MANIFEST).write_bytes(damaged)
        with pytest.raises(manifest.ManifestError, match="damaged"):
            manifest.load(rev_dir)
        for command in ("verify", "closure"):
            assert main([command, "--out", str(out), "--expected", str(inv_path)]) == 1


def test_closure_run_on_its_own_refuses_to_read_files_that_fail_verification(world, tmp_path, monkeypatch):
    from aidev_ingest import __main__ as cli
    from aidev_ingest.pin import REPO_ID

    inventory, files, out = world
    inv = {**inventory, "repo_id": REPO_ID}
    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(json.dumps(inv), encoding="utf-8")
    download_tables(inv, as_is_tables(), out, FakeFetcher(files))

    args = ["closure", "--out", str(out), "--expected", str(inv_path)]
    assert cli.main(args) == 0  # a verified folder is read, and the report is recorded
    assert "pop_closure" in manifest.load(out / REV)["checks"]

    target = out / REV / f"{as_is_tables()[0]}.parquet"
    data = bytearray(target.read_bytes())
    data[10] ^= 0x01
    target.write_bytes(bytes(data))

    def must_not_be_called(*a, **k):
        raise AssertionError("closure parsed files that fail verification")

    monkeypatch.setattr(cli, "pop_closure", must_not_be_called)
    assert cli.main(args) == 1


def test_output_folder_outside_any_repo_is_allowed(tmp_path):
    ensure_not_committable(tmp_path / "anywhere")


def test_download_refuses_an_unignored_folder_in_a_repo_before_fetching(world, tmp_path):
    inventory, files, _ = world
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(repo, "init", "-q")
    fetcher = FakeFetcher(files)
    with pytest.raises(UnsafeOutputDir):
        download_tables(inventory, as_is_tables(), repo / "data", fetcher)
    assert fetcher.calls == []


def test_every_planned_table_is_in_the_committed_inventory():
    from aidev_ingest.__main__ import DEFAULT_EXPECTED

    inventory = json.loads(DEFAULT_EXPECTED.read_text(encoding="utf-8"))
    present = expected_tables(inventory)
    assert set(as_is_tables()) <= set(present)
    assert TABLES["pr_commit_details"].drop_columns[0] in dict(present["pr_commit_details"].columns)


needs_network = pytest.mark.skipif(
    not os.environ.get("RUN_NETWORK_TESTS"), reason="set RUN_NETWORK_TESTS=1 to download from the Hub"
)


@pytest.mark.network
@needs_network
def test_real_download_of_the_two_smallest_tables(tmp_path):
    from aidev_ingest.__main__ import DEFAULT_EXPECTED
    from aidev_ingest.hub import HubSource

    inventory = json.loads(DEFAULT_EXPECTED.read_text(encoding="utf-8"))
    tables = ["related_issue", "repository"]
    out = tmp_path / "raw"
    first = download_tables(inventory, tables, out, HubSource(inventory["repo_id"]))
    assert {o.action for o in first} == {"downloaded"}
    assert manifest.verify_dir(out / inventory["revision"], inventory) == []

    second = download_tables(inventory, tables, out, HubSource(inventory["repo_id"]))
    assert {o.action for o in second} == {"skipped"}
