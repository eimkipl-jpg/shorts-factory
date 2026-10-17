"""Tests run against a throw-away data directory and a fake commercial-instance config."""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = tempfile.mkdtemp(prefix="sf_tests_")
os.environ.update(
    FACTORY_ROOT=_ROOT, ENV_FILE="/nonexistent", CREATOR_MODE="1", CREATOR_FREE_MODE="0", ADMIN_IDS="1",
    CHANNELS="creator", BOT_TOKEN="1:test", FREE_VIDEOS="1", DAILY_LIMIT="2", MAX_QUEUED="2",
    CREATOR_PACKS="10:150,30:400", REPORT_TZ="0",
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts with an empty database."""
    from common import adminstats, config, creator, db
    for f in Path(config.DB_PATH).parent.glob("*.db*"):
        f.unlink()
    adminstats._inited = False
    adminstats._visit_day.clear()
    db.init()
    creator.init()
    adminstats.init()
    yield


@pytest.fixture
def new_task():
    from common import db

    def make(kind="video"):
        tid = db.create_task("creator", kind, 100)
        db.mark_ready(tid)
        return tid
    return make
