"""Upload, in-bucket verification and delete-after-verify, against a fake bucket.

The fake behaves like the real one where it matters: a second write to the same name is
refused, and every object reports an md5. Nothing here touches Google Cloud.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from aidev_ingest import manifest
from aidev_ingest import upload as upload_module
from aidev_ingest.download import download_tables
from aidev_ingest.pin import REPO_ID, TABLES, as_is_tables
from aidev_ingest.upload import (
    MANIFESTS,
    ObjectExists,
    RemoteMismatch,
    RemoteObject,
    UploadError,
    md5_b64,
    object_name,
    upload_tables,
    verify_remote,
)
from aidev_ingest.verify import expected_tables
from helpers import REV, FakeFetcher

HELD = [t for t in as_is_tables() if TABLES[t].hold_upload]
UPLOADABLE = [t for t in as_is_tables() if not TABLES[t].hold_upload]


class Crash(Exception):
    """Stands in for the process dying."""


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.create_calls: list[str] = []
        self.text_calls: list[str] = []
        self.wrong_md5 = False  # report an md5 that does not match what was sent
        self.crash_after_create = False  # the object lands, then the process "dies"
        self.fail_next_text = False  # the manifest snapshot write dies once
        self.text_collision = False  # the snapshot name is already taken
        self.die_on_create_call: int | None = None  # the process dies on this create, before it lands

    @staticmethod
    def _md5(data: bytes) -> str:
        return base64.b64encode(hashlib.md5(data).digest()).decode("ascii")

    def _remote(self, name: str) -> RemoteObject:
        data, metadata = self.objects[name]
        md5 = self._md5(data)
        if self.wrong_md5:
            md5 = self._md5(data + b"!")
        return RemoteObject(name, len(data), md5, dict(metadata))

    def get(self, name):
        return self._remote(name) if name in self.objects else None

    def create(self, name, path, metadata):
        self.create_calls.append(name)
        if self.die_on_create_call == len(self.create_calls):
            raise Crash("the process died during this upload")
        if name in self.objects:
            raise ObjectExists(name)
        self.objects[name] = (Path(path).read_bytes(), dict(metadata))
        if self.crash_after_create:
            raise Crash("the process died after the object was created")
        return self._remote(name)

    def create_text(self, name, text):
        self.text_calls.append(name)
        if self.fail_next_text:
            self.fail_next_text = False
            raise Crash("the process died while writing the manifest snapshot")
        if self.text_collision or name in self.objects:
            raise ObjectExists(name)
        self.objects[name] = (text.encode("utf-8"), {})

    def list(self, prefix):
        return [self._remote(n) for n in sorted(self.objects) if n.startswith(prefix)]


@pytest.fixture
def ready(world):
    """Everything downloaded and verified locally, plus an empty fake bucket."""
    inventory, files, out = world
    download_tables(inventory, as_is_tables(), out, FakeFetcher(files))
    return inventory, out, FakeBucket()


def _local(out: Path, table: str) -> Path:
    return out / REV / f"{table}.parquet"


def _snapshots(bucket: FakeBucket) -> list[str]:
    return [n for n in bucket.objects if f"/{MANIFESTS}/" in n]


def test_uploads_verifies_and_deletes_local_copies_but_holds_back_the_held_table(ready):
    inventory, out, bucket = ready
    outcomes = upload_tables(inventory, as_is_tables(), out, bucket)

    assert HELD == ["pr_review_comments"], "the held table is the one the plan names"
    by_table = {o.table: o for o in outcomes}
    assert by_table["pr_review_comments"].action == "held"
    for table in UPLOADABLE:
        assert by_table[table].action == "uploaded" and by_table[table].local_deleted
        name = object_name(REV, table)
        data, metadata = bucket.objects[name]
        want = expected_tables(inventory)[table]
        assert metadata == {"sha256": want.sha256, "source_revision": REV, "table": table}
        assert len(data) == want.size
        assert not _local(out, table).exists(), "deleted only after it was verified in the bucket"
    assert _local(out, "pr_review_comments").exists(), "a held table is not touched"
    assert object_name(REV, "pr_review_comments") not in bucket.objects

    states = {t: e["state"] for t, e in manifest.load(out / REV)["tables"].items()}
    assert all(states[t] == "local_deleted" for t in UPLOADABLE)
    assert states["pr_review_comments"] == "local_verified"
    assert len(_snapshots(bucket)) == 1, "one manifest snapshot for the run"


def test_a_crash_between_deleting_the_file_and_recording_it_is_recovered_not_a_mystery(ready, monkeypatch):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    real_record = manifest.record_table

    def die_when_recording_the_deletion(rev_dir, inv, want, state="local_verified", **extra):
        if state == "local_deleted":
            raise Crash("the process died after unlink, before the record")
        return real_record(rev_dir, inv, want, state=state, **extra)

    monkeypatch.setattr(upload_module.manifest, "record_table", die_when_recording_the_deletion)
    with pytest.raises(Crash):
        upload_tables(inventory, [table], out, bucket)
    assert not _local(out, table).exists()
    assert manifest.load(out / REV)["tables"][table]["state"] == "uploaded", "the half-recorded state"

    monkeypatch.undo()
    creates = len(bucket.create_calls)
    upload_tables(inventory, [table], out, bucket)  # must not raise "needs a person"
    assert len(bucket.create_calls) == creates
    assert manifest.load(out / REV)["tables"][table]["state"] == "local_deleted"
    assert verify_remote(inventory, out, bucket) == []


def test_a_snapshot_that_could_not_be_written_is_written_by_the_next_run(ready):
    inventory, out, bucket = ready
    bucket.fail_next_text = True
    with pytest.raises(Crash):
        upload_tables(inventory, UPLOADABLE, out, bucket)
    assert all(not _local(out, t).exists() for t in UPLOADABLE), "the data itself was uploaded and verified"
    assert _snapshots(bucket) == []
    assert manifest.load(out / REV)["snapshot_pending"] is True

    upload_tables(inventory, UPLOADABLE, out, bucket)  # nothing else to do, but a snapshot is owed
    assert len(_snapshots(bucket)) == 1
    assert manifest.load(out / REV)["snapshot_pending"] is False

    upload_tables(inventory, UPLOADABLE, out, bucket)
    assert len(_snapshots(bucket)) == 1, "no further snapshots once nothing is owed"
    stored = json.loads(bucket.objects[_snapshots(bucket)[0]][0])
    assert stored["snapshot_pending"] is False, "the stored copy is the finished state"


def test_a_run_that_dies_partway_still_owes_and_later_writes_the_snapshot(ready):
    """Table 1 is uploaded and its local copy deleted; the run dies during table 2."""
    inventory, out, bucket = ready
    first, second, third = UPLOADABLE[:3]
    bucket.die_on_create_call = 2
    with pytest.raises(Crash):
        upload_tables(inventory, [first, second, third], out, bucket)

    assert not _local(out, first).exists() and _local(out, second).exists()
    assert _snapshots(bucket) == [], "the run never reached the end"
    assert manifest.load(out / REV)["snapshot_pending"] is True, "but it left the flag behind"

    bucket.die_on_create_call = None
    upload_tables(inventory, [first, second, third], out, bucket)
    assert len(_snapshots(bucket)) == 1 and manifest.load(out / REV)["snapshot_pending"] is False
    stored = json.loads(bucket.objects[_snapshots(bucket)[0]][0])
    assert {t: stored["tables"][t]["state"] for t in (first, second, third)} == dict.fromkeys(
        (first, second, third), "local_deleted"
    )
    assert verify_remote(inventory, out, bucket) == []


def test_download_does_not_refetch_a_table_whose_deletion_was_never_recorded(ready, world):
    inventory, out, bucket = ready
    _, files, _ = world
    table = UPLOADABLE[0]
    upload_tables(inventory, [table], out, bucket, keep_local=True)  # state: uploaded, file present
    _local(out, table).unlink()  # a run died after deleting the file, before recording it
    assert manifest.load(out / REV)["tables"][table]["state"] == "uploaded"

    fetcher = FakeFetcher(files)
    outcomes = download_tables(inventory, [table], out, fetcher)
    assert fetcher.calls == [] and outcomes[0].action == "uploaded"
    assert not _local(out, table).exists()


def test_verify_remote_says_when_a_snapshot_is_owed(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, UPLOADABLE, out, bucket)
    assert verify_remote(inventory, out, bucket) == []
    manifest.set_flag(out / REV, inventory, "snapshot_pending", True)
    problems = verify_remote(inventory, out, bucket)
    assert any("snapshot is owed" in p and "run `upload`" in p for p in problems), problems


def test_the_log_does_not_claim_md5_verification_when_no_md5_was_recorded(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    upload_tables(inventory, [table], out, bucket)  # uploaded, deleted, md5 recorded

    messages: list[str] = []
    upload_tables(inventory, [table], out, bucket, log=messages.append)
    assert any("verified by size, checksum and md5" in m for m in messages)

    entry = manifest.load(out / REV)["tables"][table]  # a hand-edited or older manifest: no md5
    stripped = {k: v for k, v in entry.items() if k != "remote_md5"}
    loaded = manifest.load(out / REV)
    loaded["tables"][table] = stripped
    manifest.write(out / REV, loaded)
    messages.clear()
    upload_tables(inventory, [table], out, bucket, log=messages.append)
    joined = " ".join(messages)
    assert "size and recorded checksum only" in joined and "md5" in joined
    assert "verified by size, checksum and md5" not in joined


def test_a_mismatch_message_does_not_claim_a_local_copy_exists_or_what_the_bucket_can_do(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    bucket.objects[object_name(REV, table)] = (b"other", {"sha256": "0" * 64})
    with pytest.raises(RemoteMismatch) as raised:
        upload_tables(inventory, [table], out, bucket)
    message = str(raised.value)
    assert "Any local copy is kept" in message and "needs a person" in message
    assert "cannot delete" not in message and "the local copy is kept" not in message


def test_a_snapshot_name_collision_is_a_clean_error_not_a_traceback(ready):
    inventory, out, bucket = ready
    bucket.text_collision = True
    with pytest.raises(UploadError, match="run the same command again"):
        upload_tables(inventory, UPLOADABLE[:1], out, bucket)


def test_a_second_run_creates_nothing_and_adds_no_snapshot(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, as_is_tables(), out, bucket)
    creates, texts = len(bucket.create_calls), len(bucket.text_calls)
    again = upload_tables(inventory, as_is_tables(), out, bucket)
    assert (len(bucket.create_calls), len(bucket.text_calls)) == (creates, texts)
    assert {o.action for o in again if o.table in UPLOADABLE} == {"present"}


def test_a_crash_after_upload_but_before_delete_is_finished_by_the_next_run(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    bucket.crash_after_create = True
    with pytest.raises(Crash):
        upload_tables(inventory, [table], out, bucket)
    assert object_name(REV, table) in bucket.objects and _local(out, table).exists()

    bucket.crash_after_create = False
    creates = len(bucket.create_calls)
    upload_tables(inventory, [table], out, bucket)
    assert len(bucket.create_calls) == creates, "found it in the bucket instead of uploading again"
    assert not _local(out, table).exists()
    assert manifest.load(out / REV)["tables"][table]["state"] == "local_deleted"


def test_an_existing_object_with_the_wrong_checksum_is_never_overwritten_and_the_local_file_stays(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    name = object_name(REV, table)
    bucket.objects[name] = (b"something else entirely", {"sha256": "0" * 64})
    with pytest.raises(RemoteMismatch, match="Not overwriting"):
        upload_tables(inventory, [table], out, bucket)
    assert bucket.objects[name][0] == b"something else entirely"
    assert bucket.create_calls == []
    assert _local(out, table).exists()


def test_a_local_file_that_fails_verification_is_not_uploaded_or_deleted(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    path = _local(out, table)
    data = bytearray(path.read_bytes())
    data[10] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(UploadError, match="fails verification"):
        upload_tables(inventory, [table], out, bucket)
    assert bucket.create_calls == [] and path.exists()


def test_when_the_buckets_md5_disagrees_the_local_file_is_kept(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    bucket.wrong_md5 = True
    with pytest.raises(RemoteMismatch, match="md5 differs"):
        upload_tables(inventory, [table], out, bucket)
    assert _local(out, table).exists()
    assert manifest.load(out / REV)["tables"][table]["state"] == "local_verified"


def test_a_race_that_creates_the_object_first_is_accepted_only_if_it_is_the_right_file(ready, monkeypatch):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    name = object_name(REV, table)
    want = expected_tables(inventory)[table]
    real_create = bucket.create

    def someone_else_wins_with_the_right_file(n, path, metadata):
        bucket.objects[n] = (Path(path).read_bytes(), dict(metadata))  # created just before ours
        return real_create(n, path, metadata)  # now refused as already existing

    monkeypatch.setattr(bucket, "create", someone_else_wins_with_the_right_file)
    upload_tables(inventory, [table], out, bucket)
    assert len(bucket.objects[name][0]) == want.size and not _local(out, table).exists()


def test_local_copy_gone_and_bucket_empty_is_an_error_not_a_silent_success(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    upload_tables(inventory, [table], out, bucket)
    del bucket.objects[object_name(REV, table)]
    with pytest.raises(UploadError, match="bucket has no"):
        upload_tables(inventory, [table], out, bucket)


def test_a_missing_local_file_without_an_uploaded_state_is_an_error(ready):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    _local(out, table).unlink()
    with pytest.raises(UploadError, match="does not say it was uploaded"):
        upload_tables(inventory, [table], out, bucket)


def test_the_held_table_uploads_only_when_explicitly_released(ready):
    inventory, out, bucket = ready
    held = HELD[0]
    upload_tables(inventory, [held], out, bucket)
    assert object_name(REV, held) not in bucket.objects
    upload_tables(inventory, [held], out, bucket, allow_held=(held,))
    assert object_name(REV, held) in bucket.objects and not _local(out, held).exists()
    with pytest.raises(UploadError, match="not held or not selected"):
        upload_tables(inventory, [UPLOADABLE[0]], out, bucket, allow_held=(UPLOADABLE[0],))


def test_dry_run_uploads_deletes_and_records_nothing(ready):
    inventory, out, bucket = ready
    before = manifest.load(out / REV)
    outcomes = upload_tables(inventory, as_is_tables(), out, bucket, dry_run=True)
    assert bucket.create_calls == [] and bucket.text_calls == []
    assert all(_local(out, t).exists() for t in as_is_tables())
    assert manifest.load(out / REV) == before
    assert {o.action for o in outcomes if o.table in UPLOADABLE} == {"planned"}


def test_keep_local_verifies_in_the_bucket_but_deletes_nothing(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, UPLOADABLE, out, bucket, keep_local=True)
    assert all(_local(out, t).exists() for t in UPLOADABLE)
    assert {e["state"] for t, e in manifest.load(out / REV)["tables"].items() if t in UPLOADABLE} == {"uploaded"}


def test_a_symlinked_local_file_is_refused(ready, monkeypatch):
    inventory, out, bucket = ready
    table = UPLOADABLE[0]
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == f"{table}.parquet")
    with pytest.raises(UploadError, match="symbolic link"):
        upload_tables(inventory, [table], out, bucket)
    assert bucket.create_calls == []


def test_upload_needs_a_manifest_and_known_as_is_tables(world):
    inventory, files, out = world
    with pytest.raises(UploadError, match="run `download` first"):
        upload_tables(inventory, UPLOADABLE, out, FakeBucket())
    with pytest.raises(UploadError, match="not in the inventory"):
        upload_tables(inventory, ["nope"], out, FakeBucket())
    with pytest.raises(UploadError, match="40-character"):
        upload_tables({**inventory, "revision": "../.."}, UPLOADABLE, out, FakeBucket())


def test_download_does_not_refetch_tables_that_were_uploaded_and_removed(ready, world):
    inventory, out, bucket = ready
    _, files, _ = world
    upload_tables(inventory, UPLOADABLE, out, bucket)
    fetcher = FakeFetcher(files)
    outcomes = download_tables(inventory, UPLOADABLE, out, fetcher)
    assert fetcher.calls == [] and {o.action for o in outcomes} == {"uploaded"}
    assert not any(_local(out, t).exists() for t in UPLOADABLE)


def test_redownloading_a_present_file_does_not_forget_it_was_uploaded(ready, world):
    inventory, out, bucket = ready
    _, files, _ = world
    table = UPLOADABLE[0]
    upload_tables(inventory, [table], out, bucket, keep_local=True)
    download_tables(inventory, [table], out, FakeFetcher(files))
    assert manifest.load(out / REV)["tables"][table]["state"] == "uploaded"


def test_verify_remote_passes_then_detects_each_kind_of_damage(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, as_is_tables(), out, bucket, allow_held=tuple(HELD))
    assert verify_remote(inventory, out, bucket) == []

    table = UPLOADABLE[1]
    name = object_name(REV, table)
    good = bucket.objects[name]

    bucket.objects[name] = (good[0], {**good[1], "sha256": "f" * 64})
    assert any("sha256" in p for p in verify_remote(inventory, out, bucket))

    bucket.objects[name] = (good[0] + b"x", good[1])
    problems = verify_remote(inventory, out, bucket)
    assert any("size" in p for p in problems) and any("md5" in p for p in problems)

    del bucket.objects[name]
    assert any("no such object" in p for p in verify_remote(inventory, out, bucket))

    bucket.objects[name] = good
    bucket.objects[f"raw/aidev/{REV}/stray.parquet"] = (b"x", {})
    assert any("stray.parquet" in p for p in verify_remote(inventory, out, bucket))


def test_verify_remote_needs_a_manifest_snapshot_and_ignores_snapshots_as_strays(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, UPLOADABLE, out, bucket)
    for name in _snapshots(bucket):
        del bucket.objects[name]
    assert any("no manifest snapshot" in p for p in verify_remote(inventory, out, bucket))


def test_manifest_snapshots_never_overwrite_and_hold_no_bucket_name_or_local_path(ready):
    inventory, out, bucket = ready
    upload_tables(inventory, UPLOADABLE[:3], out, bucket)
    upload_tables(inventory, UPLOADABLE[3:6], out, bucket)
    names = sorted(_snapshots(bucket))
    assert len(names) == 2 and names[0] != names[1], "each run adds a new object"
    for name in names:
        text = bucket.objects[name][0].decode("utf-8")
        assert str(out) not in text and "bucket" not in text.lower()
        assert json.loads(text)["revision"] == REV


def test_md5_matches_what_the_bucket_reports(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello")
    assert md5_b64(path) == base64.b64encode(hashlib.md5(b"hello").digest()).decode("ascii")


# --- the command line, with the real bucket replaced -------------------------------------


@pytest.fixture
def cli(world, tmp_path, monkeypatch):
    from aidev_ingest import __main__ as main_module

    inventory, files, out = world
    inv = {**inventory, "repo_id": REPO_ID}
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inv), encoding="utf-8")
    download_tables(inv, as_is_tables(), out, FakeFetcher(files))
    bucket = FakeBucket()
    names: list[str] = []
    monkeypatch.setattr(main_module, "make_bucket", lambda name: names.append(name) or bucket)
    monkeypatch.delenv("AIDEV_RAW_BUCKET", raising=False)
    return main_module.main, ["--out", str(out), "--expected", str(path)], bucket, names, inv, out


def test_the_cli_uploads_with_the_bucket_from_a_flag_or_the_environment(cli, monkeypatch):
    main, base, bucket, names, inv, out = cli
    assert main(["upload", *base, "--bucket", "my-raw-bucket", "--dry-run"]) == 0
    assert bucket.create_calls == [] and names == ["my-raw-bucket"]

    monkeypatch.setenv("AIDEV_RAW_BUCKET", "env-raw-bucket")
    assert main(["upload", *base]) == 0
    assert names[-1] == "env-raw-bucket"
    assert object_name(REV, UPLOADABLE[0]) in bucket.objects
    assert main(["verify-remote", *base]) == 0


@pytest.mark.parametrize("bad", ["", "UPPER", "a", "has space", "x" * 70, "-leading", "../x"])
def test_the_cli_refuses_a_missing_or_malformed_bucket_name(cli, bad):
    main, base, bucket, names, inv, out = cli
    flag = [f"--bucket={bad}"] if bad else []  # `=` form: a value starting with `-` is not read as an option
    assert main(["upload", *base, *flag]) == 2 and names == []
    assert main(["verify-remote", *base, *flag]) == 2


def test_the_cli_reports_held_tables_and_errors_without_a_traceback(cli, capsys):
    main, base, bucket, names, inv, out = cli
    assert main(["upload", *base, "--bucket", "my-raw-bucket"]) == 0
    printed = capsys.readouterr()
    assert "held back: pr_review_comments" in printed.out and "HELD" in printed.err
    (out / REV / "manifest.json").write_text("not json", encoding="utf-8")
    assert main(["upload", *base, "--bucket", "my-raw-bucket"]) == 1


# --- the real adapter, against a stand-in for Google's library -----------------------------


def _fake_google(
    monkeypatch, *, existing: dict, precondition_fails: bool, upload_raises=None, credentials_missing=False
):
    # The real hierarchy matters: a refused overwrite IS a GoogleAPIError, and the adapter must
    # still tell it apart from a network failure.
    class GoogleAPIError(Exception):
        pass

    class PreconditionFailed(GoogleAPIError):
        pass

    class RetryError(GoogleAPIError):
        pass

    class RequestException(Exception):
        pass

    class Forbidden(GoogleAPIError):
        code = 403  # real api_core errors carry the HTTP status

    class DefaultCredentialsError(Exception):
        pass

    class FakeBlob:
        def __init__(self, name, size=3, md5="AAAA", metadata=None):
            self.name, self.size, self.md5_hash, self.metadata = name, size, md5, metadata
            self.uploaded_with: dict = {}

        def upload_from_filename(self, filename, **kwargs):
            if upload_raises is not None:
                raise {
                    "retry": RetryError,
                    "connection": ConnectionAbortedError,
                    "request": RequestException,
                    "forbidden": Forbidden,
                }[upload_raises]()
            if precondition_fails:
                raise PreconditionFailed()
            self.uploaded_with = kwargs

        upload_from_string = lambda self, text, **kwargs: self.upload_from_filename("", **kwargs)  # noqa: E731

        def reload(self):
            pass

    class FakeBucketApi:
        def __init__(self):
            self.made: list[FakeBlob] = []

        def get_blob(self, name):
            return existing.get(name)

        def blob(self, name, chunk_size=None):
            blob = FakeBlob(name)
            self.made.append(blob)
            return blob

    class FakeClient:
        def __init__(self):
            if credentials_missing:
                raise DefaultCredentialsError()
            self.api = FakeBucketApi()

        def bucket(self, name):
            self.bucket_name = name
            return self.api

        def list_blobs(self, bucket, prefix):
            return [b for n, b in existing.items() if n.startswith(prefix)]

    class FakeRetry:
        def __init__(self, deadline=120.0):
            self.deadline = deadline

        def with_deadline(self, deadline):
            return FakeRetry(deadline)

    api_core = types.ModuleType("google.api_core")
    api_core.exceptions = types.SimpleNamespace(
        GoogleAPIError=GoogleAPIError, PreconditionFailed=PreconditionFailed, RetryError=RetryError
    )
    retry_module = types.SimpleNamespace(
        DEFAULT_RETRY=FakeRetry(),
        is_generation_specified=object(),
        ConditionalRetryPolicy=lambda policy, predicate, kwargs: types.SimpleNamespace(policy=policy),
    )
    cloud = types.ModuleType("google.cloud")
    cloud.storage = types.SimpleNamespace(Client=FakeClient, retry=retry_module)
    media = types.SimpleNamespace(
        common=types.SimpleNamespace(InvalidResponse=type("InvalidResponse", (Exception,), {}), DataCorruption=type("DataCorruption", (Exception,), {}))
    )
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    auth.exceptions = types.SimpleNamespace(DefaultCredentialsError=DefaultCredentialsError)
    google.api_core, google.cloud, google.resumable_media, google.auth = api_core, cloud, media, auth
    requests = types.ModuleType("requests")
    requests.exceptions = types.SimpleNamespace(RequestException=RequestException)
    for name, module in {
        "google": google,
        "google.auth": auth,
        "google.auth.exceptions": auth.exceptions,
        "google.api_core": api_core,
        "google.api_core.exceptions": api_core.exceptions,
        "google.cloud": cloud,
        "google.cloud.storage": cloud.storage,
        "google.cloud.storage.retry": retry_module,
        "google.resumable_media": media,
        "google.resumable_media.common": media.common,
        "requests": requests,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return FakeBlob, FakeClient


def test_the_gcs_adapter_creates_only_if_absent_checks_md5_and_maps_a_refusal(tmp_path, monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    FakeBlob, _ = _fake_google(monkeypatch, existing={}, precondition_fails=False)
    bucket = GcsBucket("some-bucket")
    path = tmp_path / "f.parquet"
    path.write_bytes(b"abc")
    created = bucket.create("raw/x.parquet", path, {"sha256": "s"})
    sent = bucket._bucket.made[-1]
    assert sent.uploaded_with["if_generation_match"] == 0, "the server refuses an overwrite"
    assert sent.uploaded_with["checksum"] == "md5", "the client checks what arrived"
    from aidev_ingest import gcs

    policy = sent.uploaded_with["retry"].policy
    assert policy.deadline == gcs.RETRY_DEADLINE_SECONDS > 120.0, "retries outlast Google's 2-minute default"
    assert sent.metadata == {"sha256": "s"} and created.name == "raw/x.parquet"

    FakeBlob, _ = _fake_google(monkeypatch, existing={}, precondition_fails=True)
    bucket = GcsBucket("some-bucket")
    with pytest.raises(ObjectExists):
        bucket.create("raw/x.parquet", path, {})
    with pytest.raises(ObjectExists):
        bucket.create_text("raw/m.json", "{}")


@pytest.mark.parametrize("kind", ["retry", "connection", "request"])
def test_a_network_failure_becomes_a_short_error_that_says_rerun_is_safe(tmp_path, monkeypatch, kind):
    """The real run dropped its connection mid-upload and printed a 40-line traceback."""
    from aidev_ingest.gcs import GcsBucket

    _fake_google(monkeypatch, existing={}, precondition_fails=False, upload_raises=kind)
    path = tmp_path / "f.parquet"
    path.write_bytes(b"abc")
    with pytest.raises(UploadError, match="(?i)run the same command again") as raised:
        GcsBucket("some-bucket").create("raw/x.parquet", path, {})
    message = str(raised.value)
    assert "nothing local was deleted by this step" in message.lower()
    assert "may exist" in message, "does not claim an interrupted upload always leaves nothing behind"
    assert not isinstance(raised.value, ObjectExists)
    assert "Probably the network" in message


def test_a_403_is_reported_as_google_refusing_not_as_the_network(tmp_path, monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    _fake_google(monkeypatch, existing={}, precondition_fails=False, upload_raises="forbidden")
    path = tmp_path / "f.parquet"
    path.write_bytes(b"abc")
    with pytest.raises(UploadError) as raised:
        GcsBucket("some-bucket").create("raw/x.parquet", path, {})
    message = str(raised.value)
    assert "HTTP 403" in message and "Google refused the request" in message
    assert "Probably the network" not in message
    assert "run the same command again" in message.lower()


def test_missing_google_credentials_is_a_clear_message_naming_the_fix(monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    _fake_google(monkeypatch, existing={}, precondition_fails=False, credentials_missing=True)
    with pytest.raises(UploadError, match="gcloud auth application-default login"):
        GcsBucket("some-bucket")


def test_the_cli_reports_a_missing_library_or_credentials_without_a_traceback(cli, capsys, monkeypatch):
    from aidev_ingest import __main__ as main_module

    main, base, bucket, names, inv, out = cli
    for failure in (RuntimeError("google-cloud-storage is not installed"), UploadError("no Google credentials found")):
        def broken(name, failure=failure):
            raise failure

        monkeypatch.setattr(main_module, "make_bucket", broken)
        assert main(["upload", *base, "--bucket", "my-raw-bucket"]) == 2
        assert "error:" in capsys.readouterr().err


def test_a_refused_overwrite_is_still_reported_as_already_existing_not_as_a_network_problem(tmp_path, monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    _fake_google(monkeypatch, existing={}, precondition_fails=True)
    path = tmp_path / "f.parquet"
    path.write_bytes(b"abc")
    with pytest.raises(ObjectExists):  # it is a GoogleAPIError too; it must not be translated
        GcsBucket("some-bucket").create("raw/x.parquet", path, {})


def test_the_chunks_are_small_so_a_dropped_connection_costs_little():
    from aidev_ingest import gcs

    assert gcs.CHUNK_SIZE == 8 * 1024 * 1024 and gcs.CHUNK_SIZE % (256 * 1024) == 0


def test_the_gcs_adapter_reads_objects_and_lists_by_prefix(monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    probe = types.SimpleNamespace
    existing = {
        "raw/a": probe(name="raw/a", size=5, md5_hash="M", metadata={"sha256": "s"}),
        "other/b": probe(name="other/b", size=1, md5_hash=None, metadata=None),
    }
    _fake_google(monkeypatch, existing=existing, precondition_fails=False)
    bucket = GcsBucket("some-bucket")
    assert bucket.get("missing") is None
    got = bucket.get("raw/a")
    assert (got.size, got.md5_b64, got.metadata) == (5, "M", {"sha256": "s"})
    assert [o.name for o in bucket.list("raw/")] == ["raw/a"]


def test_a_clear_message_when_the_google_library_is_missing(monkeypatch):
    from aidev_ingest.gcs import GcsBucket

    for name in ("google", "google.api_core", "google.cloud", "google.cloud.storage", "google.api_core.exceptions"):
        monkeypatch.setitem(sys.modules, name, None)  # makes `import` raise ImportError
    with pytest.raises(RuntimeError, match="pip install google-cloud-storage"):
        GcsBucket("some-bucket")


def test_the_upload_module_has_no_google_import_at_load_time():
    """CI installs no Google library; importing the package and running the offline tests must work."""
    assert "google" not in upload_module.__dict__
