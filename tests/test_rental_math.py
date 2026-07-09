"""Tests for the pure date/fee math in rental.py: late fees and due-date
computation across a DST transition. Both are called out in the health audit
as high-value, low-effort coverage since they're pure functions with real
correctness risk (timezone edge cases, day-boundary rounding).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rental


# ---------- compute_late_fee ----------

def test_late_fee_returned_before_due_is_zero():
    due = "2026-06-10T21:00:00+00:00"
    returned = "2026-06-10T20:59:59+00:00"
    assert rental.compute_late_fee(due, returned) == 0.0


def test_late_fee_returned_exactly_on_due_is_zero():
    due = "2026-06-10T21:00:00+00:00"
    assert rental.compute_late_fee(due, due) == 0.0


def test_late_fee_one_second_late_charges_one_day():
    due = "2026-06-10T21:00:00+00:00"
    returned = "2026-06-10T21:00:01+00:00"
    assert rental.compute_late_fee(due, returned) == 1.0


def test_late_fee_just_under_24h_late_charges_one_day():
    due = "2026-06-10T21:00:00+00:00"
    returned = "2026-06-11T20:59:59+00:00"
    assert rental.compute_late_fee(due, returned) == 1.0


def test_late_fee_multi_day_late():
    due = "2026-06-10T21:00:00+00:00"
    returned = "2026-06-13T09:00:00+00:00"  # 2 days 12 hours late
    assert rental.compute_late_fee(due, returned) == 3.0


def test_late_fee_invalid_input_is_zero():
    assert rental.compute_late_fee("not-a-date", "also-not-a-date") == 0.0


# ---------- compute_due_at ----------

def test_due_at_is_five_days_later_at_9pm_local():
    rented_at = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    due = rental.compute_due_at(rented_at, "America/Los_Angeles")
    due_local = due.astimezone(rental._rental_timezone("America/Los_Angeles"))
    assert due_local.date() == rented_at.astimezone(
        rental._rental_timezone("America/Los_Angeles")
    ).date() + timedelta(days=rental.RENTAL_DURATION_DAYS)
    assert due_local.hour == rental.RENTAL_DUE_HOUR_LOCAL


def test_due_at_naive_datetime_is_treated_as_utc():
    rented_naive = datetime(2026, 6, 1, 15, 0)
    rented_aware = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    assert rental.compute_due_at(rented_naive, "America/Los_Angeles") == rental.compute_due_at(
        rented_aware, "America/Los_Angeles"
    )


def test_due_at_preserves_local_wall_clock_across_spring_forward():
    """A rental spanning the March 2026 US spring-forward (Mar 8, 2am -> 3am)
    must still land at 9pm *local* time on the due date, not 9pm shifted by
    the hour the clocks skipped.
    """
    rented_at = datetime(2026, 3, 5, 18, 0, tzinfo=timezone.utc)  # before the transition
    due = rental.compute_due_at(rented_at, "America/Los_Angeles")
    la_tz = rental._rental_timezone("America/Los_Angeles")
    due_local = due.astimezone(la_tz)

    assert due_local.date() == datetime(2026, 3, 10).date()
    assert due_local.hour == rental.RENTAL_DUE_HOUR_LOCAL
    # Due date is after the transition, so the offset should be PDT (UTC-7),
    # not the PST (UTC-8) offset that was in effect when the rental started.
    assert due_local.utcoffset() == timedelta(hours=-7)


def test_due_at_falls_back_to_default_timezone_when_unset():
    rented_at = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
    default_due = rental.compute_due_at(rented_at, None)
    explicit_due = rental.compute_due_at(rented_at, rental.default_timezone_name())
    assert default_due == explicit_due
