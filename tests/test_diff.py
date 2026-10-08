from bookwatch import diff, report, storage
from test_storage import book, failed_run, ok_run


def kinds(changes):
    return [(c.kind, c.title) for c in changes]


def test_price_moves_sorted_biggest_drop_first(conn):
    ok_run(conn, [book("A", 1000), book("B", 1000), book("C", 1000), book("D", 1000)])
    ok_run(conn, [book("A", 800), book("B", 950), book("C", 1100), book("D", 1000)])

    old, new, changes = diff.latest_changes(conn)

    assert (old.id, new.id) == (1, 2)
    assert kinds(changes) == [("price_drop", "A"), ("price_drop", "B"), ("price_rise", "C")]
    assert changes[0].pct == -20.0
    assert changes[0].to_dict()["pct"] == -20.0


def test_min_pct_filters_small_moves(conn):
    ok_run(conn, [book("A", 1000), book("B", 1000)])
    ok_run(conn, [book("A", 800), book("B", 990)])
    _, _, changes = diff.latest_changes(conn, min_pct=5)
    assert kinds(changes) == [("price_drop", "A")]


def test_stock_flips_are_reported(conn):
    ok_run(conn, [book("A", in_stock=False), book("B", in_stock=True)])
    ok_run(conn, [book("A", in_stock=True), book("B", in_stock=False)])
    _, _, changes = diff.latest_changes(conn)
    assert kinds(changes) == [("back_in_stock", "A"), ("sold_out", "B")]


def test_new_means_never_seen_before_not_just_missing_from_last_run(conn):
    ok_run(conn, [book("X"), book("Y")])
    ok_run(conn, [book("X")], complete=False)  # short crawl did not reach Y
    ok_run(conn, [book("X"), book("Y"), book("Z")])

    _, _, changes = diff.latest_changes(conn)
    assert kinds(changes) == [("new", "Z")]
    assert changes[0].pct is None


def test_removed_only_reported_after_a_complete_crawl(conn):
    ok_run(conn, [book("X"), book("Y")])
    partial = ok_run(conn, [book("X")], complete=False)
    full = ok_run(conn, [book("X")], complete=True)
    again = ok_run(conn, [book("X")], complete=True)

    assert diff.compare(conn, storage.get_run(conn, partial)) == []
    assert kinds(diff.compare(conn, storage.get_run(conn, full))) == [("removed", "Y")]
    assert diff.compare(conn, storage.get_run(conn, again)) == []  # reported once


def test_changes_hidden_by_a_partial_crawl_are_still_caught(conn):
    # Run 1 sees everything; run 2 only reaches A; run 3 sees B cheaper and sold out, Y gone.
    ok_run(conn, [book("A"), book("B", 1000), book("Y")])
    ok_run(conn, [book("A")], complete=False)
    ok_run(conn, [book("A"), book("B", 500, in_stock=False)])

    _, _, changes = diff.latest_changes(conn)
    assert kinds(changes) == [("price_drop", "B"), ("sold_out", "B"), ("removed", "Y")]


def test_rise_from_zero_is_reported_and_rendered(conn):
    ok_run(conn, [book("Freebie", 0)])
    ok_run(conn, [book("Freebie", 500)])

    _, _, changes = diff.latest_changes(conn, min_pct=50)
    (change,) = changes
    assert change.kind == "price_rise"
    assert change.pct is None
    assert "(from free)" in report.format_change(change)
    assert change.to_dict()["pct"] is None


def test_min_pct_threshold_is_exact(conn):
    ok_run(conn, [book("A", 100)])
    ok_run(conn, [book("A", 129)])  # 29 %: a float computation gives 28.999999999999996
    _, _, changes = diff.latest_changes(conn, min_pct=29)
    assert kinds(changes) == [("price_rise", "A")]


def test_needs_two_successful_runs(conn):
    assert diff.latest_changes(conn) == (None, None, [])
    ok_run(conn, [book()])
    old, new, changes = diff.latest_changes(conn)
    assert old is None
    assert new.id == 1
    assert changes == []


def test_failed_runs_are_skipped_when_comparing(conn):
    ok_run(conn, [book("A", 1000)])
    failed_run(conn, [book("A", 1)])
    ok_run(conn, [book("A", 900)])

    old, new, changes = diff.latest_changes(conn)
    assert (old.id, new.id) == (1, 3)
    assert [(c.old_price_cents, c.new_price_cents) for c in changes] == [(1000, 900)]
