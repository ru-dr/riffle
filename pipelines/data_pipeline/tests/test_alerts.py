import json
import logging
import smtplib
import urllib.error
from types import SimpleNamespace

import pytest

from scripts import alerts


def write_report(path, anomalies):
    errors = sum(a["severity"] == "error" for a in anomalies)
    path.write_text(json.dumps({"errors": errors, "warnings": len(anomalies) - errors, "anomalies": anomalies}), encoding="utf-8")
    return path


def anomaly(severity="warn", n=1):
    return {"severity": severity, "kind": "outlier", "table": "repository", "column": "stars", "message": "big", "count": n, "total": 100}


def test_no_anomalies_means_no_alert(tmp_path):
    assert alerts.summarise_anomalies(write_report(tmp_path / "a.json", [])) is None


def test_summary_counts_errors_and_warnings_and_lists_them(tmp_path):
    subject, body = alerts.summarise_anomalies(write_report(tmp_path / "a.json", [anomaly("error"), anomaly("warn")]))
    assert subject == "Riffle data pipeline: 1 errors, 1 warnings"
    assert "[ERROR] outlier repository.stars: big (1 of 100)" in body and "[WARN]" in body


def test_a_long_list_is_cut_with_a_pointer_to_the_report(tmp_path):
    _, body = alerts.summarise_anomalies(write_report(tmp_path / "a.json", [anomaly() for _ in range(alerts.MAX_LINES + 3)]))
    assert body.splitlines()[-1] == "... and 3 more in a.json"


def test_with_no_channel_configured_it_only_logs(caplog):
    with caplog.at_level(logging.WARNING, logger="alerts"):
        assert alerts.send_alert("subject", "body", env={}) == []
    assert "no alert channel configured" in caplog.text and "ALERT subject" in caplog.text


def test_slack_gets_a_json_post_to_the_webhook(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"ok"

    def fake_urlopen(request, timeout):
        seen["url"], seen["body"], seen["timeout"] = request.full_url, json.loads(request.data), timeout
        return Response()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    sent = alerts.send_alert("s", "b", env={"RIFFLE_SLACK_WEBHOOK_URL": "https://hooks.example/abc"})
    assert sent == ["slack"] and seen["url"] == "https://hooks.example/abc"
    assert "*s*" in seen["body"]["text"] and seen["timeout"] == alerts.TIMEOUT_SECONDS


def test_a_webhook_that_is_not_https_is_refused_without_a_request(monkeypatch, caplog):
    monkeypatch.setattr(alerts.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not be called"))
    assert alerts.send_alert("s", "b", env={"RIFFLE_SLACK_WEBHOOK_URL": "http://insecure.example/x"}) == []
    assert "must start with https" in caplog.text


def test_a_slack_failure_is_logged_without_the_secret_url_and_not_raised(monkeypatch, caplog):
    def boom(*a, **k): raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(alerts.urllib.request, "urlopen", boom)
    assert alerts.send_alert("s", "b", env={"RIFFLE_SLACK_WEBHOOK_URL": "https://hooks.example/SECRETTOKEN"}) == []
    assert "slack alert failed" in caplog.text and "SECRETTOKEN" not in caplog.text


def test_email_uses_starttls_login_and_sends(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): calls.append(("starttls",))
        def login(self, user, password): calls.append(("login", user))
        def send_message(self, message): calls.append(("send", message["To"], message["Subject"]))

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    env = {"RIFFLE_SMTP_HOST": "mail.example", "RIFFLE_SMTP_USER": "u", "RIFFLE_SMTP_PASSWORD": "p",
           "RIFFLE_ALERT_FROM": "a@example.com", "RIFFLE_ALERT_TO": "b@example.com"}
    assert alerts.send_alert("subj", "body", env=env) == ["email"]
    assert calls == [("connect", "mail.example", 587), ("starttls",), ("login", "u"), ("send", "b@example.com", "subj")]


def test_an_email_failure_is_logged_not_raised(monkeypatch, caplog):
    def boom(*a, **k): raise smtplib.SMTPException("refused")

    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
    env = {"RIFFLE_SMTP_HOST": "m", "RIFFLE_ALERT_FROM": "a@example.com", "RIFFLE_ALERT_TO": "b@example.com"}
    assert alerts.send_alert("s", "b", env=env) == []
    assert "email alert failed" in caplog.text


def test_a_half_configured_email_is_not_attempted(monkeypatch):
    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda *a, **k: pytest.fail("must not connect"))
    assert alerts.send_alert("s", "b", env={"RIFFLE_SMTP_HOST": "m"}) == []


def test_failure_callback_names_the_task_the_run_and_the_reason(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda subject, body, env=None: sent.append((subject, body)))
    context = {"task_instance": SimpleNamespace(task_id="validate", dag_id="riffle_data_pipeline"),
               "run_id": "manual__1", "exception": ValueError("schema broke")}
    alerts.failure_callback(context)
    assert sent == [("Airflow task failed: riffle_data_pipeline.validate", "run: manual__1\nreason: schema broke")]


def test_failure_callback_survives_an_empty_context(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda subject, body, env=None: sent.append(subject))
    alerts.failure_callback({})
    assert sent == ["Airflow task failed: unknown dag.unknown task"]
