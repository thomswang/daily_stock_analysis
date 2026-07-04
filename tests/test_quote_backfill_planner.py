# -*- coding: utf-8 -*-
"""Tests for quote backfill range planner."""

from __future__ import annotations

from datetime import date

from src.services.quote_backfill_planner import resolve_effective_start


def test_resolve_uses_list_date() -> None:
    eff, reason = resolve_effective_start(
        "001335",
        date(2024, 1, 1),
        date(2026, 7, 3),
        list_date=date(2025, 4, 15),
    )
    assert eff == date(2025, 4, 15)
    assert reason == "list_date"


def test_resolve_list_date_past_end() -> None:
    eff, reason = resolve_effective_start(
        "001335",
        date(2024, 1, 1),
        date(2025, 1, 1),
        list_date=date(2025, 4, 15),
    )
    assert eff is None
    assert reason == "list_date_past_end"


def test_resolve_without_list_date_uses_start() -> None:
    eff, reason = resolve_effective_start(
        "001335",
        date(2024, 1, 1),
        date(2026, 7, 3),
    )
    assert eff == date(2024, 1, 1)
    assert reason == "no_list_date"


def test_resolve_force_ignores_list_date() -> None:
    eff, reason = resolve_effective_start(
        "001335",
        date(2024, 1, 1),
        date(2026, 7, 3),
        list_date=date(2025, 4, 15),
        force=True,
    )
    assert eff == date(2024, 1, 1)
    assert reason == "no_list_date"
