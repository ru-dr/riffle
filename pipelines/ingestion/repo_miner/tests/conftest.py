"""Keep a developer's real .env (store URL, admin token, GitHub token) out of the tests: config.py
loads .env on import, and some tests would otherwise talk to the real store."""
import os

os.environ["RIFFLE_NO_DOTENV"] = "1"
for _k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN", "RIFFLE_ADMIN_TOKEN"):
    os.environ.pop(_k, None)
os.environ["RIFFLE_NO_AUTO_UPDATE"] = "1"      # tests never pull code; test_auto_update.py turns it back on for its own temp repos
os.environ.setdefault("RIFFLE_NO_DOCTOR", "1")       # importing tui.py runs the environment doctor; on a runner without every package it would exit and end pytest
os.environ.setdefault("RIFFLE_NO_PLAN_USAGE", "1")
os.environ["RIFFLE_NO_API_CACHE"] = "1"       # tests never write a repo cache; test_api_cache.py turns it on in a temp dir


# A test run started as a background job (`pytest &`, or a CI step that backgrounds it) inherits SIGINT as ignored, and every
# child a test starts would then ignore a Stop. The stop tests need the normal handler.
import signal as _signal  # noqa: E402

try:
    if _signal.getsignal(_signal.SIGINT) == _signal.SIG_IGN:
        _signal.signal(_signal.SIGINT, _signal.default_int_handler)
except (ValueError, OSError):
    pass
