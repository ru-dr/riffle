"""Keep a developer's real .env (store URL, admin token, GitHub token) out of the tests: config.py
loads .env on import, and some tests would otherwise talk to the real store."""
import os

os.environ["RIFFLE_NO_DOTENV"] = "1"
for _k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN", "RIFFLE_ADMIN_TOKEN"):
    os.environ.pop(_k, None)
os.environ["RIFFLE_DATA_TTL_S"] = "0"            # the data window (a 10 minute cache) is off unless a test turns it on
os.environ["RIFFLE_LOKI_MAX_AGE_H"] = "100000000"   # the tests' fixed timestamps must not age out of Loki's window; test_loki.py tests the window itself
