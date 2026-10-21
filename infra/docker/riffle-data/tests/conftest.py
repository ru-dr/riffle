"""Keep a developer's real .env (store URL, admin token, GitHub token) out of the tests: config.py
loads .env on import, and some tests would otherwise talk to the real store."""
import os

os.environ["RIFFLE_NO_DOTENV"] = "1"
for _k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN", "RIFFLE_ADMIN_TOKEN"):
    os.environ.pop(_k, None)
