"""Keep a developer's real .env (store URL, admin token, GitHub token) out of the tests: config.py
loads .env on import, and some tests would otherwise talk to the real store."""
import os

os.environ["RIFFLE_NO_DOTENV"] = "1"
for _k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN", "RIFFLE_ADMIN_TOKEN"):
    os.environ.pop(_k, None)
os.environ["RIFFLE_NO_AUTO_UPDATE"] = "1"      # tests never pull code; test_auto_update.py turns it back on for its own temp repos
