"""The model in service: the registered model's alias (champion) in MLflow, downloaded and kept in memory.

register_model.py (pipelines/training/outcome) registers each version with its folder `model/`, which holds
`seen_repos/` and `new_repos/`. The scorer serves one variant: new_repos, the model for repositories it was not
trained on (every tenant repository, ADR 0001: global first). Every CHECK_SECONDS it asks MLflow which version the
alias points to; when it changed, the new version is downloaded and loaded beside the old one and then swapped in
one step, so a request always sees one whole model (one pinned model_version). If MLflow cannot be reached or the
new version does not load, the loaded one stays in service.

The files come through the MLflow server (--serve-artifacts), so the scorer needs no access to the models bucket.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass

from . import predict

log = logging.getLogger("scorer.models")


@dataclass(frozen=True)
class Loaded:
    model_version: str          # e.g. riffle-outcome-setup-g/7/new_repos
    version: str                # the registry version, e.g. 7
    variant: predict.Variant
    miner_version: str | None   # the model version's tag miner_version, when it was set


class ModelStore:
    def __init__(self, tracking_uri: str, name: str = "riffle-outcome-setup-g", alias: str = "champion",
                 variant: str = "new_repos", cache_dir: str | None = None, client=None, download=None):
        self.name, self.alias, self.variant = name, alias, variant
        self.cache_dir = cache_dir or tempfile.gettempdir()
        self._client = client
        self._download = download
        self._tracking_uri = tracking_uri
        self._current: Loaded | None = None
        self._lock = threading.Lock()
        self.attempted = threading.Event()     # set after the first check, whatever its result

    @property
    def current(self) -> Loaded | None:
        return self._current        # one read of one reference: a request keeps the model it got

    def client(self):
        if self._client is None:
            from mlflow.tracking import MlflowClient
            self._client = MlflowClient(tracking_uri=self._tracking_uri)
        return self._client

    def download(self, uri: str, dst: str) -> str:
        if self._download is not None:
            return self._download(uri, dst)
        import mlflow.artifacts
        return mlflow.artifacts.download_artifacts(artifact_uri=uri, dst_path=dst,
                                                   tracking_uri=self._tracking_uri)

    def refresh(self) -> bool:
        """Load the alias's version if it is not the one in service. True when a new model was swapped in."""
        with self._lock:
            try:
                mv = self.client().get_model_version_by_alias(self.name, self.alias)
                if self._current is not None and self._current.version == str(mv.version):
                    return False
                dst = tempfile.mkdtemp(prefix=f"model-{mv.version}-", dir=self.cache_dir)
                try:
                    path = self.download(f"{mv.source.rstrip('/')}/{self.variant}", dst)
                    loaded = Loaded(model_version=f"{self.name}/{mv.version}/{self.variant}", version=str(mv.version),
                                    variant=predict.Variant(path),
                                    miner_version=(mv.tags or {}).get("miner_version"))
                finally:
                    shutil.rmtree(dst, ignore_errors=True)      # the model is in memory; the files are not needed
                old, self._current = self._current, loaded
                log.info("model in service", extra={"model_version": loaded.model_version,
                                                    "replaced": old.model_version if old else None})
                return True
            except Exception:  # noqa: BLE001  MLflow down, no champion yet, or a model that does not load
                log.exception("model check failed; keeping %s",
                              self._current.model_version if self._current else "no model")
                return False
            finally:
                self.attempted.set()


def from_env() -> ModelStore:
    return ModelStore(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow.riffle-ml.svc:5000"),
                      name=os.environ.get("MODEL_NAME", "riffle-outcome-setup-g"),
                      alias=os.environ.get("MODEL_ALIAS", "champion"),
                      variant=os.environ.get("MODEL_VARIANT", "new_repos"))
