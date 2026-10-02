from datetime import date, timedelta

from src import alerts
from src.alerts import Finding, check_allocation_drift, check_total_return
from src.models import HoldingState, Target


def make_state(drift_pp, band=10):
    t = Target("A", "Alpha", 0.5, "IDR", "yfinance", 0.08, "Broker", 100, 0.10)
    weight = 0.5 + drift_pp / 100
    return HoldingState(target=t, shares=0, avg_cost=0, price=0, value_native=0, value_idr=0,
                         weight=weight, drift_pp=drift_pp)


def test_check_allocation_drift_flags_beyond_band():
    findings = check_allocation_drift([make_state(30.0)], band_pp=10)
    assert len(findings) == 1
    assert findings[0].check == "drift"
    assert findings[0].severity == "warning"  # 30pp > 1.5x the 10pp band


def test_check_allocation_drift_watch_not_warning_when_just_over_band():
    findings = check_allocation_drift([make_state(11.0)], band_pp=10)
    assert findings[0].severity == "watch"  # over the band, but not by a lot


def test_check_allocation_drift_no_flag_within_band():
    assert check_allocation_drift([make_state(5.0)], band_pp=10) == []


def test_check_total_return_below_floor():
    findings = check_total_return(0.05, xirr_reliable=True, floor=0.07)
    assert len(findings) == 1
    assert findings[0].check == "total_return"


def test_check_total_return_suppressed_when_unreliable():
    # PLAN.md's explicit rule: an early, noisy XIRR reading shouldn't fire a false alarm
    assert check_total_return(0.02, xirr_reliable=False) == []


def test_check_total_return_no_flag_above_floor():
    assert check_total_return(0.10, xirr_reliable=True, floor=0.07) == []


def test_annotate_repeats_marks_second_consecutive_occurrence(tmp_path):
    from src import storage
    yesterday = date.today() - timedelta(days=1)
    storage.append_alerts_log(tmp_path, [Finding("A", "A", "warning", "msg", check="drift")], yesterday)
    findings = [Finding("A", "A", "warning", "msg2", check="drift")]
    result = alerts.annotate_repeats(findings, tmp_path)
    assert result[0].repeat is True


def test_annotate_repeats_false_on_first_ever_run(tmp_path):
    findings = [Finding("A", "A", "warning", "msg", check="drift")]
    result = alerts.annotate_repeats(findings, tmp_path)
    assert result[0].repeat is False


def test_annotate_repeats_false_when_different_check_type(tmp_path):
    from src import storage
    yesterday = date.today() - timedelta(days=1)
    storage.append_alerts_log(tmp_path, [Finding("A", "A", "warning", "msg", check="drift")], yesterday)
    findings = [Finding("A", "A", "warning", "msg2", check="dividend_cut")]  # different check
    result = alerts.annotate_repeats(findings, tmp_path)
    assert result[0].repeat is False


# --- pending_review_from_log (the `sav buy` review banner — see cli.render_review_banner) ---

def test_pending_review_never_run_is_stale_with_no_tier2(tmp_path):
    status = alerts.pending_review_from_log(tmp_path)
    assert status.last_run_date is None
    assert status.tier2 == []
    assert status.stale is True


def test_pending_review_flags_dividend_cut_as_tier2_even_on_first_run(tmp_path):
    from src import storage
    today = date.today()
    storage.append_alerts_log(
        tmp_path, [Finding("A", "ASII.JK", "warning", "cut", check="dividend_cut")], today
    )
    status = alerts.pending_review_from_log(tmp_path)
    assert ("ASII.JK", "dividend_cut") in status.tier2


def test_pending_review_flags_a_flag_repeated_two_runs_in_a_row(tmp_path):
    from src import storage
    yesterday = date.today() - timedelta(days=1)
    today = date.today()
    storage.append_alerts_log(tmp_path, [Finding("A", "KO", "watch", "m1", check="chowder_number")], yesterday)
    storage.append_alerts_log(tmp_path, [Finding("A", "KO", "watch", "m2", check="chowder_number")], today)
    status = alerts.pending_review_from_log(tmp_path)
    assert ("KO", "chowder_number") in status.tier2


def test_pending_review_does_not_flag_a_one_off_soft_finding(tmp_path):
    from src import storage
    today = date.today()
    storage.append_alerts_log(tmp_path, [Finding("A", "KO", "watch", "m1", check="chowder_number")], today)
    status = alerts.pending_review_from_log(tmp_path)
    assert status.tier2 == []


def test_pending_review_stale_after_threshold(tmp_path):
    from src import storage
    old_run = date.today() - timedelta(days=40)
    storage.append_alerts_log(tmp_path, [Finding("A", "KO", "watch", "m1", check="chowder_number")], old_run)
    status = alerts.pending_review_from_log(tmp_path, stale_after_days=35)
    assert status.stale is True
    assert status.days_since == 40


def test_pending_review_not_stale_when_recent(tmp_path):
    from src import storage
    recent = date.today() - timedelta(days=5)
    storage.append_alerts_log(tmp_path, [Finding("A", "KO", "watch", "m1", check="chowder_number")], recent)
    status = alerts.pending_review_from_log(tmp_path, stale_after_days=35)
    assert status.stale is False


# --- deep_review_status (the unified sav alerts "go to a live session" trigger — covers
# both the main stocks and the temp_rest fund, never an auto re-search) -------------------

def test_deep_review_due_when_never_logged(tmp_path):
    due, days_since = alerts.deep_review_status(tmp_path)
    assert due is True
    assert days_since is None


def test_deep_review_not_due_when_recently_logged(tmp_path):
    from src import storage
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=5), "initial review")
    due, days_since = alerts.deep_review_status(tmp_path)
    assert due is False
    assert days_since == 5


def test_deep_review_due_after_explicit_threshold(tmp_path):
    from src import storage
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=40), "initial review")
    due, days_since = alerts.deep_review_status(tmp_path, review_after_days=30)
    assert due is True
    assert days_since == 40


def test_deep_review_default_cadence_is_yearly(tmp_path):
    from src import storage
    # 40 days ago shouldn't be due under the yearly (365-day) default.
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=40), "initial review")
    due, days_since = alerts.deep_review_status(tmp_path)
    assert due is False
    assert days_since == 40


def test_deep_review_due_past_a_year(tmp_path):
    from src import storage
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=400), "initial review")
    due, days_since = alerts.deep_review_status(tmp_path)
    assert due is True
    assert days_since == 400


def test_deep_review_uses_most_recent_of_multiple_entries(tmp_path):
    from src import storage
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=400), "old review")
    storage.append_deep_review_log(tmp_path, date.today() - timedelta(days=10), "newer review")
    due, days_since = alerts.deep_review_status(tmp_path)
    assert due is False
    assert days_since == 10
