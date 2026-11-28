"""One PrFeatures message in, one score_results row and two messages out (ScoreResult, ScoreLog).

    parse and check  ->  already stored? publish what is stored  ->  score (or unranked with a reason)
                     ->  store (Postgres, key delivery_id)  ->  publish score-results and score-log

Fail open: a PR is always stored and published, ranked or unranked with a reason. Unranked reasons:
  invalid_input            the row breaks its contract, or was made by a miner version the model was not trained with
  not_outside_contributor  a bot or a confirmed maintainer: the model is trained and evaluated on outside contributors
  model_unavailable        no model loaded (none registered yet, or MLflow never answered)
  scoring_failed           scoring raised on MAX_ATTEMPTS deliveries in a row (until then the message is retried)
The message is stored before it is published, so a crash in between republishes the stored row on redelivery, never
a second score. The model's inputs are not stored in Postgres (ADR 0024: feature vectors would grow the transactional
store without bound); they go only to the score log. So a republished score sends its ScoreResult again but not its
score log row, which is counted in riffle_score_log_dropped_total like any other lost log row. The LLM is never
called here; explanation stays null until the explainer is built.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from prometheus_client import Counter

from . import predict, ranking

log = logging.getLogger("scorer")

MAX_ATTEMPTS = 3
SCORE_LOG_DROPPED = Counter("riffle_score_log_dropped_total", "Score log rows not published (ADR 0024)")
IDS = ("delivery_id", "tenant_id", "repo", "pr_number", "head_sha", "action")


class Retry(Exception):
    """Nack the message: Pub/Sub delivers it again after its backoff."""


def contracts_dir() -> Path:
    if os.environ.get("CONTRACTS_DIR"):
        return Path(os.environ["CONTRACTS_DIR"])
    here = Path(__file__).resolve()
    for p in (here.parents[1] / "contracts", here.parents[3] / "contracts"):    # image, then repo checkout
        if (p / "pr_features.schema.json").is_file():
            return p
    raise FileNotFoundError("contracts/ not found; set CONTRACTS_DIR")


class Contracts:
    def __init__(self, root: Path | None = None):
        root = root or contracts_dir()
        load = lambda n: json.loads((root / f"{n}.schema.json").read_text(encoding="utf-8"))
        fc = jsonschema.Draft202012Validator.FORMAT_CHECKER
        self.pr_features = jsonschema.Draft202012Validator(load("pr_features"), format_checker=fc)
        self.mined_dataset = jsonschema.Draft202012Validator(load("mined_dataset"), format_checker=fc)
        self.version = {n: load(n)["x-riffle-version"] for n in ("score_result", "score_log")}

    def problems(self, msg: dict) -> list[str]:
        errs = [f"{list(e.path)}: {e.message}" for e in self.pr_features.iter_errors(msg)]
        row = msg.get("row")
        if isinstance(row, dict):
            errs += [f"row{list(e.path)}: {e.message}" for e in self.mined_dataset.iter_errors(row)]
            errs += [f"row.{k} differs from {k}" for k in ("repo", "pr_number", "head_sha") if row.get(k) != msg.get(k)]
        return errs


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def same_major(a: str | None, b: str | None) -> bool:
    """A miner MAJOR version changes the columns or their meaning (pipelines/ingestion/repo_miner/version.py)."""
    return a is None or b is None or a.split(".")[0] == b.split(".")[0]


class Scorer:
    def __init__(self, models, history, store, publisher, contracts: Contracts, now=utcnow,
                 max_attempts: int = MAX_ATTEMPTS):
        self.models, self.history, self.store, self.publisher = models, history, store, publisher
        self.contracts, self.now, self.max_attempts = contracts, now, max_attempts

    def handle(self, data: bytes, delivery_attempt: int | None = None) -> str:
        """Returns what happened (ranked, an unranked reason, duplicate, dropped). Raises Retry to be redelivered;
        any other exception (store or Pub/Sub down) also means: not done, redeliver."""
        try:
            msg = json.loads(data)
        except ValueError:
            log.error("not JSON: dropped")
            return "dropped"
        if not isinstance(msg, dict) or any(not isinstance(msg.get(k), (str, int)) for k in IDS) \
                or not isinstance(msg.get("pr_number"), int):
            log.error("no delivery_id, tenant_id, repo, pr_number, head_sha or action: dropped")
            return "dropped"
        ctx = {"delivery_id": msg["delivery_id"], "tenant_id": msg["tenant_id"]}

        stored = self.store.get(msg["tenant_id"], msg["delivery_id"])
        if stored is not None:
            self.publish(stored, features=None)
            log.info("already scored: republished", extra=ctx)
            return "duplicate"

        record = self.score(msg, delivery_attempt, ctx)
        features = record.pop("features")       # to the score log only, never to Postgres
        if self.store.insert(record):
            self.publish(record, features=features or {})
        else:                                   # stored meanwhile by another delivery, which logs it
            record = self.store.get(msg["tenant_id"], msg["delivery_id"]) or record
            self.publish(record, features=None)
        outcome = record["unranked_reason"] or "ranked"
        log.info("scored", extra={**ctx, "result": outcome, "model_version": record["model_version"],
                                  "rank_band": record["rank_band"]})
        return outcome

    def score(self, msg: dict, delivery_attempt: int | None, ctx: dict) -> dict:
        record = base_record(msg, self.now())
        problems = self.contracts.problems(msg)
        if problems:
            log.warning("invalid input: %s", "; ".join(problems[:5]), extra=ctx)
            return unranked(record, "invalid_input")
        row = msg["row"]
        if not predict.is_outside_contributor(row):
            return unranked(record, "not_outside_contributor")
        model = self.models.current
        if model is None:
            return unranked(record, "model_unavailable")
        record["model_version"] = model.model_version
        if not same_major(msg["miner_version"], model.miner_version):
            log.warning("row from miner %s, model trained on miner %s", msg["miner_version"], model.miner_version,
                        extra=ctx)
            return unranked(record, "invalid_input")
        try:
            history = self.history.rows(msg["tenant_id"], msg["repo"])
            p = predict.predict(model.variant, history, row)
            score = ranking.rank_score(p.p_lands_clean, p.p_lands_after_rework)
            recent = self.store.recent_rank_scores(msg["tenant_id"], msg["repo"], msg["pr_number"], ranking.RANK_MODE,
                                                   ranking.RECENT_DAYS, ranking.RECENT_LIMIT)
        except Exception as e:  # noqa: BLE001
            if (delivery_attempt or 1) < self.max_attempts:
                log.exception("scoring failed on attempt %s: retrying", delivery_attempt, extra=ctx)
                raise Retry(str(e)) from e
            log.exception("scoring failed on attempt %s: stored unranked", delivery_attempt, extra=ctx)
            return unranked(record, "scoring_failed")
        record.update({
            "p_lands_clean": p.p_lands_clean, "p_lands_after_rework": p.p_lands_after_rework,
            "p_goes_nowhere": p.p_goes_nowhere, "rank_score": score, "rank_band": ranking.band(score, recent),
            "predictions": {**{f"p_reason_{k}": v for k, v in p.reasons.items()},
                            **{f"p_label_{k}": v for k, v in p.labels.items()}},
            "features": p.features})
        return record

    def publish(self, record: dict, features: dict | None):
        """score-results must be stored by Pub/Sub, or the message is redelivered. score-log is never on the scoring
        path (ADR 0024): if it fails the score still goes out, and riffle_score_log_dropped_total counts the loss.
        features None (a republished score: its inputs are not stored) sends no score log row and counts it."""
        attrs = {"delivery_id": record["delivery_id"], "tenant_id": record["tenant_id"]}
        result = self.publisher.publish("score-results", score_result(record),
                                        {**attrs, "schema": f"score_result/{self.contracts.version['score_result']}"})
        logged = None
        if features is not None:
            try:
                logged = self.publisher.publish("score-log", score_log(record, features),
                                                {**attrs, "schema": f"score_log/{self.contracts.version['score_log']}"})
            except Exception:  # noqa: BLE001
                logged = None
        result.result(timeout=30)
        try:
            if logged is None:
                raise RuntimeError("score log row not sent" if features is not None
                                   else "republished score: its inputs are not stored")
            logged.result(timeout=30)
        except Exception:  # noqa: BLE001
            SCORE_LOG_DROPPED.inc()
            log.warning("score log not published: dropped", exc_info=True, extra=attrs)


def base_record(msg: dict, now: str) -> dict:
    return {"delivery_id": msg["delivery_id"], "tenant_id": msg["tenant_id"], "repo": msg["repo"],
            "pr_number": msg["pr_number"], "head_sha": msg["head_sha"], "trigger": msg["action"],
            "p_lands_clean": None, "p_lands_after_rework": None, "p_goes_nowhere": None,
            "rank_score": None, "rank_mode": ranking.RANK_MODE, "rank_band": None, "model_version": None,
            "miner_version": msg.get("miner_version") if isinstance(msg.get("miner_version"), str) else None,
            "layer_weight": 0.0, "predictions": None, "features": {}, "explanation": None,
            "unranked_reason": None, "scored_at": now}


def unranked(record: dict, reason: str) -> dict:
    return {**record, "rank_score": None, "rank_band": None, "unranked_reason": reason}


PREDICTION_KEYS = ("p_reason_abandoned", "p_reason_duplicate", "p_reason_unwanted", "p_reason_ci_technical",
                   "p_reason_unknown", "p_label_bad", "p_label_revert", "p_label_hotfix", "p_label_szz_bug")
COMMON = ("delivery_id", "tenant_id", "repo", "pr_number", "head_sha", "trigger", "scored_at", "model_version",
          "miner_version", "rank_score", "rank_band", "layer_weight", "unranked_reason")


def score_result(r: dict) -> dict:
    """contracts/score_result.schema.json"""
    pred = r.get("predictions") or {}
    clean, rework = r["p_lands_clean"], r["p_lands_after_rework"]
    return {**{k: r[k] for k in COMMON},
            "p_lands_clean": clean, "p_lands_after_rework": rework, "p_goes_nowhere": r["p_goes_nowhere"],
            "p_merge": min(1.0, clean + rework) if clean is not None and rework is not None else None,
            **{k: pred.get(k) for k in PREDICTION_KEYS},
            "rank_mode": r["rank_mode"], "explanation": r["explanation"]}


def score_log(r: dict, features: dict) -> dict:
    """contracts/score_log.schema.json"""
    return {**{k: r[k] for k in COMMON}, "features": features,
            **{k: r[k] for k in ("p_lands_clean", "p_lands_after_rework", "p_goes_nowhere")}}
