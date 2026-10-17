"""Usage analytics: who came, what they did, funnel, source tags, admin exclusion, CSV export."""
from common import adminstats as A
from common import creator, db


def test_first_touch_creates_user_and_flags_new():
    u, new = A.touch(100, "Anna", "anna", "start", "insta")
    assert new and u["username"] == "anna"
    u, new = A.touch(100, "Anna", "anna", "cb", "cr:dl")
    assert not new and u["actions"] == 2


def test_source_tag_only_from_first_touch():
    A.touch(101, "B", "", "start", "insta")
    A.set_src(101, "insta")
    A.set_src(101, "tiktok")
    assert A.user_card(101)["src"] == "insta"


def test_source_tag_is_sanitised():
    A.touch(102, "C")
    A.set_src(102, "a b;DROP TABLE--" * 5)
    src = A.user_card(102)["src"]
    assert all(ch.isalnum() or ch in "_-" for ch in src) and len(src) <= 32


def test_admins_are_excluded_from_numbers():
    A.touch(1, "Owner", "", "cb", "cr:home")                  # ADMIN_IDS=1
    A.touch(200, "Real user", "", "cb", "cr:dl")
    assert A.total_users() == 1
    assert A.overview("all")["total"] == 1


def test_one_visit_event_per_day():
    for _ in range(5):
        A.touch(201, "D", "", "cb", "cr:dl")
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM cr_events WHERE user_id=201 AND ev='visit'").fetchone()[0] == 1


def test_funnel_and_task_outcomes(new_task):
    creator.add_credits(202, 5, "grant")
    A.touch(202, "E", "", "cb", "cr:dl")
    done, failed = new_task("dl"), new_task("dl")
    creator.reserve(202, done, "dl")
    creator.reserve(202, failed, "dl")
    with db.connect() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (done,))
        c.execute("UPDATE tasks SET status='failed' WHERE id=?", (failed,))
    r = A.overview("all")
    assert r["kinds"]["dl"] == {"done": 1, "failed": 1, "work": 0}
    assert (r["f_new"], r["f_section"], r["f_task"], r["f_done"]) == (1, 1, 1, 1)
    text = A.overview_text("all")
    assert "успешных 50%" in text


def test_user_card_lists_tasks_and_titles(new_task):
    A.touch(203, "F", "", "cb", "sm:home")
    creator.add_credits(203, 1, "grant")
    creator.reserve(203, new_task())
    creator.log_submit(203, "How the Roman roads were built")
    card = A.user_text(203)
    assert "Roman roads" in card and "Умный монтаж" in card


def test_csv_export_has_bom_header_and_rows():
    A.touch(204, "Иван", "ivan", "start", "blog")
    A.set_src(204, "blog")
    raw = A.users_csv()
    assert raw.startswith(b"\xef\xbb\xbf")                    # BOM so Excel opens Cyrillic correctly
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("user_id;")
    assert "ivan" in text and "blog" in text


def test_daily_report_when_nobody_came():
    assert "никто не заходил" in A.daily_report_text()


def test_spark_scales_to_max():
    assert A.spark([0, 5, 10])[-1] == "█" and A.spark([0, 0]) == "▁▁"
