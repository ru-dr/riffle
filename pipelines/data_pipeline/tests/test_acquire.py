
import pytest
from helpers import REV, FakeFetcher

from scripts import acquire

GOOD = f"aidev:\n  commit: {REV}\n  tables: pull_request,repository\n"


def write_params(tmp_path, text):
    path = tmp_path / "params.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# params.yaml


def test_params_accept_a_comma_string(tmp_path):
    assert acquire.load_params(write_params(tmp_path, GOOD)) == {
        "commit": REV,
        "tables": ["pull_request", "repository"],
    }


def test_params_accept_a_list_and_trim_blanks(tmp_path):
    text = f"aidev:\n  commit: {REV}\n  tables: [' repository ', '']\n"
    assert acquire.load_params(write_params(tmp_path, text))["tables"] == ["repository"]


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty file
        "- just\n- a list\n",
        "other: 1\n",  # no aidev section
        "aidev: 5\n",
        "aidev:\n  commit: v5\n  tables: repository\n",  # a tag is not a commit
        f"aidev:\n  commit: {REV.upper()}\n  tables: repository\n",
        f"aidev:\n  commit: {REV}\n  tables: ''\n",  # empty table list
        f"aidev:\n  commit: {REV}\n  tables: ' , '\n",
        f"aidev:\n  commit: {REV}\n  tables: [1, 2]\n",
        f"aidev:\n  commit: {REV}\n",  # tables missing
        "aidev: [unclosed\n",  # invalid YAML
    ],
)
def test_bad_params_are_refused(tmp_path, text):
    with pytest.raises(acquire.ParamsError):
        acquire.load_params(write_params(tmp_path, text))


def test_missing_params_file_is_refused(tmp_path):
    with pytest.raises(acquire.ParamsError, match="does not exist"):
        acquire.load_params(tmp_path / "nope.yaml")


# run()


def test_run_downloads_verifies_and_a_second_run_skips(world):
    inventory, fetcher, expected, params, out = world
    assert acquire.run(params, out, expected, source=fetcher) == 0
    assert sorted(fetcher.calls) == ["pull_request", "repository"]
    assert (out / REV / "repository.parquet").exists()
    assert (out / REV / "manifest.json").exists()

    fetcher.calls.clear()
    assert acquire.run(params, out, expected, source=fetcher) == 0
    assert fetcher.calls == []  # everything already verified


def test_a_commit_that_is_not_the_inventorys_is_refused_before_any_download(world):
    _, fetcher, expected, params, out = world
    params["commit"] = "d" * 40
    assert acquire.run(params, out, expected, source=fetcher) == 1
    assert fetcher.calls == []
    assert not out.exists()


def test_dry_run_writes_nothing(world):
    _, fetcher, expected, params, out = world
    assert acquire.run(params, out, expected, source=fetcher, dry_run=True) == 0
    assert fetcher.calls == []
    assert not (out / REV).exists()


def test_an_unknown_table_is_an_error(world):
    _, fetcher, expected, params, out = world
    params["tables"] = ["not_a_table"]
    with pytest.raises(acquire.DownloadError, match="not in the inventory"):
        acquire.run(params, out, expected, source=fetcher)


def test_a_flipped_byte_in_a_placed_file_is_never_overwritten(world):
    _, fetcher, expected, params, out = world
    acquire.run(params, out, expected, source=fetcher)
    target = out / REV / "repository.parquet"
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0xFF
    target.write_bytes(bytes(data))
    with pytest.raises(acquire.DownloadError):
        acquire.run(params, out, expected, source=fetcher)
    assert target.read_bytes() == bytes(data)  # left exactly as found


# main(): exit codes and the log file


def test_main_succeeds_and_writes_the_log(world, tmp_path, monkeypatch):
    _, fetcher, expected, params, out = world
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(acquire, "HubSource", lambda repo: fetcher)
    code = acquire.main(
        ["--params", str(write_params(tmp_path, GOOD)), "--out", str(out), "--expected", str(expected)]
    )
    assert code == 0
    text = (tmp_path / "logs" / "acquire.log").read_text(encoding="utf-8")
    assert "ok: 2 tables verified" in text


def test_main_returns_1_and_logs_when_params_are_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    code = acquire.main(["--params", str(tmp_path / "missing.yaml")])
    assert code == 1
    assert "acquire failed" in (tmp_path / "logs" / "acquire.log").read_text(encoding="utf-8")


def test_main_returns_1_when_the_network_drops(world, tmp_path, monkeypatch):
    _, fetcher, expected, _, out = world
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "logs"))
    flaky = FakeFetcher(fetcher.files, fail_on="repository")
    monkeypatch.setattr(acquire, "HubSource", lambda repo: flaky)
    code = acquire.main(
        ["--params", str(write_params(tmp_path, GOOD)), "--out", str(out), "--expected", str(expected)]
    )
    assert code == 1
    assert not (out / REV / "repository.parquet").exists()  # no half file placed
