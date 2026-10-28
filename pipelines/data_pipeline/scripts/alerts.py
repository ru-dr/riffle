"""Alerts: Slack and email, configured only through environment variables.

Nothing here is a secret in the repository. Put the values in the gitignored .env that Docker
Compose reads (see infra/docker/airflow/.env.example); when none is set, an alert is only logged.

  RIFFLE_SLACK_WEBHOOK_URL                            an incoming-webhook URL
  RIFFLE_SMTP_HOST, RIFFLE_SMTP_PORT (587),
  RIFFLE_SMTP_USER, RIFFLE_SMTP_PASSWORD              the mail server (STARTTLS when offered)
  RIFFLE_ALERT_FROM, RIFFLE_ALERT_TO                  sender and comma-separated recipients

STARTTLS is used whenever the server offers it. A server that does not (a local mail catcher such as
Mailpit) is only used when no SMTP user is set, so a password is never sent unencrypted.

Only the standard library is used, so the Airflow image needs no provider package. A failure
to send is logged and never raised: an alert that breaks the pipeline would be worse than none.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any

log = logging.getLogger("alerts")

TIMEOUT_SECONDS = 15
MAX_LINES = 15


def summarise_anomalies(report: Path) -> tuple[str, str] | None:
    """(subject, body) for an anomalies.json, or None when it lists nothing."""
    document = json.loads(report.read_text(encoding="utf-8"))
    found = document.get("anomalies", [])
    if not found:
        return None
    errors, warnings = document.get("errors", 0), document.get("warnings", 0)
    lines = [
        f"[{a['severity'].upper()}] {a['kind']} {a['table']}.{a['column']}: {a['message']} ({a['count']} of {a['total']})"
        for a in found[:MAX_LINES]
    ]
    if len(found) > MAX_LINES:
        lines.append(f"... and {len(found) - MAX_LINES} more in {report.name}")
    return f"Riffle data pipeline: {errors} errors, {warnings} warnings", "\n".join(lines)


def _slack(subject: str, body: str, url: str) -> None:
    payload = json.dumps({"text": f"*{subject}*\n```{body}```"}).encode("utf-8")
    request = urllib.request.Request(url, payload, {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 (https URL from env)
        response.read()


def _email(subject: str, body: str, env: dict[str, str]) -> None:
    message = EmailMessage()
    message["Subject"], message["From"] = subject, env["RIFFLE_ALERT_FROM"]
    message["To"] = env["RIFFLE_ALERT_TO"]
    message.set_content(body)
    with smtplib.SMTP(env["RIFFLE_SMTP_HOST"], int(env.get("RIFFLE_SMTP_PORT") or 587), timeout=TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls()
            smtp.ehlo()
        elif env.get("RIFFLE_SMTP_USER"):
            # A password must never cross the network in the clear. Plain SMTP is only for a server that
            # needs no login, such as a local mail catcher like Mailpit.
            raise smtplib.SMTPNotSupportedError("the server does not offer STARTTLS; refusing to send a password without it")
        if env.get("RIFFLE_SMTP_USER"):
            smtp.login(env["RIFFLE_SMTP_USER"], env.get("RIFFLE_SMTP_PASSWORD", ""))
        smtp.send_message(message)


def send_alert(subject: str, body: str, env: dict[str, str] | None = None) -> list[str]:
    """Send to every configured channel. Returns the channels that worked ('slack', 'email')."""
    env = dict(os.environ if env is None else env)
    sent: list[str] = []
    configured = False

    url = env.get("RIFFLE_SLACK_WEBHOOK_URL", "").strip()
    if url:
        configured = True
        if not url.startswith("https://"):
            log.error("slack alert not sent: the webhook URL must start with https://")
        else:
            try:
                _slack(subject, body, url)
                sent.append("slack")
            except (urllib.error.URLError, OSError) as error:  # the URL itself is a secret, so it is not logged
                log.error("slack alert failed: %s", type(error).__name__)

    if all(env.get(k) for k in ("RIFFLE_SMTP_HOST", "RIFFLE_ALERT_FROM", "RIFFLE_ALERT_TO")):
        configured = True
        try:
            _email(subject, body, env)
            sent.append("email")
        except (smtplib.SMTPException, OSError) as error:
            log.error("email alert failed: %s", type(error).__name__)

    if not configured:
        log.warning("no alert channel configured; alert only logged: %s", subject)
    log.warning("ALERT %s | %s", subject, body.replace("\n", " | "))
    return sent


def failure_callback(context: dict[str, Any]) -> None:
    """Airflow on_failure_callback: say which task of which DAG run failed and why."""
    instance = context.get("task_instance")
    task_id = getattr(instance, "task_id", "unknown task")
    dag_id = getattr(instance, "dag_id", None) or getattr(context.get("dag"), "dag_id", "unknown dag")
    run_id = context.get("run_id") or getattr(context.get("dag_run"), "run_id", "unknown run")
    reason = context.get("exception")
    send_alert(f"Airflow task failed: {dag_id}.{task_id}", f"run: {run_id}\nreason: {reason or 'see the task log'}")
