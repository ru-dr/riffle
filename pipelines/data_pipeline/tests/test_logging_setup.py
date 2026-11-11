import logging

from scripts import logging_setup


def test_logs_go_to_the_named_file_in_the_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path / "out"))
    path = logging_setup.setup_logging("demo")
    logging.getLogger("demo").info("hello from the test")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert path == tmp_path / "out" / "demo.log"
    text = path.read_text(encoding="utf-8")
    assert "INFO demo hello from the test" in text
    assert text.split()[0].endswith("Z")  # UTC timestamp


def test_calling_it_twice_does_not_duplicate_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFFLE_LOG_DIR", str(tmp_path))
    logging_setup.setup_logging("demo")
    path = logging_setup.setup_logging("demo")
    logging.getLogger("demo").warning("once")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert path.read_text(encoding="utf-8").count("once") == 1


def test_default_log_dir_is_the_pipeline_logs_folder(monkeypatch):
    monkeypatch.delenv("RIFFLE_LOG_DIR", raising=False)
    assert logging_setup.log_dir() == logging_setup.ROOT / "logs"
