"""Credits, free demo, daily limits, refunds and payment idempotency of the commercial bot."""
from common import creator, db


def test_new_user_gets_free_demo_with_watermark(new_task):
    assert creator.free_left(creator.get(10)) == 1
    ok, why = creator.check(10)
    assert ok, why
    assert creator.reserve(10, new_task()) is True            # True = demo, watermark applies
    assert creator.free_left(creator.get(10)) == 0
    ok, why = creator.check(10)
    assert not ok and why == "no_credits"


def test_paid_video_spends_one_credit_without_watermark(new_task):
    creator.add_credits(11, 3, "grant")
    assert creator.reserve(11, new_task()) is False
    assert creator.get(11)["credits"] == 2


def test_refund_returns_credit_exactly_once(new_task):
    creator.add_credits(12, 1, "grant")
    tid = new_task()
    creator.reserve(12, tid)
    assert creator.get(12)["credits"] == 0
    assert creator.refund(tid) == 12
    assert creator.get(12)["credits"] == 1
    assert creator.refund(tid) is None                         # second refund is ignored
    assert creator.get(12)["credits"] == 1


def test_refund_of_free_demo_frees_the_slot(new_task):
    tid = new_task()
    creator.reserve(13, tid)
    creator.refund(tid)
    assert creator.free_left(creator.get(13)) == 1


def test_payment_is_idempotent_by_charge_id():
    assert creator.add_credits(14, 10, "buy", "charge-1") is True
    assert creator.add_credits(14, 10, "buy", "charge-1") is False   # Telegram may redeliver the update
    assert creator.get(14)["credits"] == 10


def test_revoke_payment_never_goes_below_zero(new_task):
    creator.add_credits(15, 10, "buy", "charge-2")
    creator.reserve(15, new_task())                            # spends 1 -> 9 left
    assert creator.revoke_payment("charge-2") == (15, 9)
    assert creator.get(15)["credits"] == 0
    assert creator.revoke_payment("charge-2") is None


def test_daily_limit_blocks_extra_videos(new_task):
    creator.add_credits(16, 10, "grant")
    for _ in range(2):                                         # DAILY_LIMIT=2 in the test config
        assert creator.check(16)[0]
        creator.reserve(16, new_task())
        with db.connect() as c:
            c.execute("UPDATE tasks SET status='done'")        # finished, so MAX_QUEUED does not interfere
    ok, why = creator.check(16)
    assert not ok and "лимит" in why.lower()


def test_failed_video_does_not_count_towards_daily_limit(new_task):
    creator.add_credits(17, 10, "grant")
    tid = new_task()
    creator.reserve(17, tid)
    creator.refund(tid)
    assert creator.today_count(17) == 0


def test_per_user_queue_cap(new_task):
    creator.add_credits(18, 10, "grant")
    creator.reserve(18, new_task())
    creator.reserve(18, new_task())
    ok, why = creator.check(18)                                # MAX_QUEUED=2 tasks in progress
    assert not ok


def test_banned_user_is_rejected_and_admin_is_not_limited():
    creator.set_ban(19, True)
    assert creator.check(19) == (False, "Доступ закрыт.")
    assert creator.check(1) == (True, "")                      # ADMIN_IDS=1


def test_pack_prices():
    assert creator.pack_price(10) == 150
    assert creator.pack_price(30) == 400
    assert creator.pack_price(7) is None


def test_delete_user_data_wipes_name_and_events():
    from common import adminstats
    adminstats.touch(20, "Anna", "anna", "cb", "cr:dl")
    creator.delete_user_data(20)
    u = creator.get(20)
    assert u["name"] == "" and u["username"] == ""
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM cr_events WHERE user_id=20").fetchone()[0] == 0
