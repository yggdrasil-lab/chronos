"""Unit tests for daytime nap recovery blocks and evening sleep-pressure nudges."""

from datetime import date
from zoneinfo import ZoneInfo

import httpx

from app.domain.fasting import EirDailyVitalsSnapshot
from app.domain.models import Commitment, CommitmentSource, GridBlock, NudgeMode
from app.domain.passive_signals import PeerFetchStatus
from app.domain.schedule_engine import compute_now_next, resolve_day_schedule
from app.domain.sleep_recovery import (
    EirNapRecord,
    NapRecoveryBlock,
    build_nap_recovery_blocks,
    evaluate_sleep_pressure_nudge,
)
from app.services.peer_gateways import HttpEirGateway

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
TARGET_DATE = date(2026, 11, 27)


def test_http_eir_gateway_parses_naps_and_handles_malformed_entries() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sleep_duration_hours": 6.64,
                "nap_duration_hours": 2.18,
                "nap_count": 1,
                "naps": [
                    {
                        "start_at": "2026-11-27T16:29:00+13:00",
                        "end_at": "2026-11-27T18:39:00+13:00",
                        "duration_hours": 2.18,
                    },
                    "not-a-dict",
                    {"start_at": "", "end_at": "2026-11-27T15:00:00+13:00", "duration_hours": 0.5},
                    {"start_at": "2026-11-27T12:00:00+13:00", "end_at": None, "duration_hours": 0.5},
                    {
                        "start_at": "2026-11-27T12:00:00+13:00",
                        "end_at": "2026-11-27T12:30:00+13:00",
                        "duration_hours": "bad",
                    },
                    {
                        "start_at": "2026-11-27T12:00:00+13:00",
                        "end_at": "2026-11-27T12:30:00+13:00",
                        "duration_hours": -0.5,
                    },
                ],
            },
        )

    vitals = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://eir-api:8000"),
    ).fetch_daily_vitals(TARGET_DATE)

    assert vitals.status.available is True
    assert vitals.nap_duration_hours == 2.18
    assert vitals.nap_count == 1
    assert vitals.naps == (
        EirNapRecord(
            start_at="2026-11-27T16:29:00+13:00",
            end_at="2026-11-27T18:39:00+13:00",
            duration_hours=2.18,
        ),
    )

    null_naps = HttpEirGateway(
        base_url="http://eir-api:8000",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(200, json={"sleep_duration_hours": 7.5, "naps": None})
            ),
            base_url="http://eir-api:8000",
        ),
    ).fetch_daily_vitals(TARGET_DATE)
    assert null_naps.nap_duration_hours is None
    assert null_naps.nap_count == 0
    assert null_naps.naps == ()


def test_build_nap_recovery_blocks_converts_tz_sorts_and_ignores_malformed() -> None:
    vitals = EirDailyVitalsSnapshot(
        status=PeerFetchStatus(available=True),
        nap_duration_hours=2.68,
        nap_count=2,
        naps=(
            EirNapRecord(
                start_at="2026-11-27T03:29:00Z",  # 16:29 NZDT (UTC+13)
                end_at="2026-11-27T05:39:00Z",    # 18:39 NZDT
                duration_hours=2.18,
            ),
            EirNapRecord(
                start_at="not-an-iso-ts",
                end_at="2026-11-27T12:00:00+13:00",
                duration_hours=0.5,
            ),
            EirNapRecord(
                start_at="2026-11-27T13:00:00+13:00",
                end_at="2026-11-27T12:30:00+13:00",
                duration_hours=0.5,
            ),
            EirNapRecord(
                start_at="2026-11-27T11:30:00+13:00",
                end_at="2026-11-27T12:00:00+13:00",
                duration_hours=0.5,
            ),
        ),
    )
    blocks = build_nap_recovery_blocks(vitals, AUCKLAND_TZ)
    assert len(blocks) == 2
    assert blocks[0].start_minute == 690
    assert blocks[0].end_minute == 720
    assert blocks[1].start_minute == 989
    assert blocks[1].end_minute == 1119
    assert blocks[1].duration_hours == 2.18

    unavailable = EirDailyVitalsSnapshot(status=PeerFetchStatus(available=False, error="down"))
    assert build_nap_recovery_blocks(unavailable, AUCKLAND_TZ) == ()
    assert build_nap_recovery_blocks(None, AUCKLAND_TZ) == ()


def test_evaluate_sleep_pressure_nudge_thresholds_and_copy() -> None:
    assert evaluate_sleep_pressure_nudge(()) is None

    # 14:59 (899) + 89m (1.483h -> 1.48h) -> None
    early_short = (
        NapRecoveryBlock(
            start_at="2026-11-27T13:30:00+13:00",
            end_at="2026-11-27T14:59:00+13:00",
            start_minute=810,
            end_minute=899,
            duration_hours=89 / 60,
        ),
    )
    assert evaluate_sleep_pressure_nudge(early_short) is None

    # 15:00 (900) + 30m -> triggered (late only)
    late_short = (
        NapRecoveryBlock(
            start_at="2026-11-27T14:30:00+13:00",
            end_at="2026-11-27T15:00:00+13:00",
            start_minute=870,
            end_minute=900,
            duration_hours=0.5,
        ),
    )
    late_nudge = evaluate_sleep_pressure_nudge(late_short, nap_duration_hours=0.5)
    assert late_nudge is not None
    assert late_nudge.triggered is True
    assert late_nudge.latest_nap_end == "15:00"
    assert late_nudge.total_nap_hours == 0.5
    assert late_nudge.nap_count == 1
    assert late_nudge.reason == "Late nap ending 15:00 may lower evening sleep pressure."
    assert (
        late_nudge.suggestion
        == "Keep 22:00 screens-off calm, avoid forcing early sleep, and dim lights until drowsy."
    )

    # 14:59 (899) + 90m (1.5h) -> triggered (long only)
    early_long = (
        NapRecoveryBlock(
            start_at="2026-11-27T13:29:00+13:00",
            end_at="2026-11-27T14:59:00+13:00",
            start_minute=809,
            end_minute=899,
            duration_hours=1.5,
        ),
    )
    long_nudge = evaluate_sleep_pressure_nudge(early_long, nap_duration_hours=1.5)
    assert long_nudge is not None
    assert long_nudge.latest_nap_end == "14:59"
    assert long_nudge.reason == "Extended 1.5h nap (ended 14:59) may lower evening sleep pressure."

    # 18:39 (1119) + 2.18h -> triggered (late & long)
    late_long = (
        NapRecoveryBlock(
            start_at="2026-11-27T16:29:00+13:00",
            end_at="2026-11-27T18:39:00+13:00",
            start_minute=989,
            end_minute=1119,
            duration_hours=2.18,
        ),
    )
    both_nudge = evaluate_sleep_pressure_nudge(late_long, nap_duration_hours=2.18)
    assert both_nudge is not None
    assert both_nudge.latest_nap_end == "18:39"
    assert both_nudge.total_nap_hours == 2.18
    assert both_nudge.reason == "Late 2.2h nap ending 18:39 may delay evening sleep onset."


def test_naps_do_not_carve_slices_or_trigger_conflicts_and_propagate_to_now_next() -> None:
    block = GridBlock(
        id=1,
        title="Afternoon Focus",
        day_of_week=TARGET_DATE.weekday(),
        start_minute=960,   # 16:00
        end_minute=1140,    # 19:00
        category="focus",
        nudge_mode=NudgeMode.NONE,
    )
    commitment = Commitment(
        id=10,
        title="Team Sync",
        target_date=TARGET_DATE,
        start_minute=1020,  # 17:00
        end_minute=1080,    # 18:00
        all_day=False,
        source=CommitmentSource.MANUAL,
    )
    nap = NapRecoveryBlock(
        start_at="2026-11-27T16:29:00+13:00",
        end_at="2026-11-27T18:39:00+13:00",
        start_minute=989,
        end_minute=1119,
        duration_hours=2.18,
    )
    nudge = evaluate_sleep_pressure_nudge((nap,), nap_duration_hours=2.18)
    base_schedule = resolve_day_schedule(TARGET_DATE, [block], [], [commitment])
    from dataclasses import replace

    schedule = replace(base_schedule, naps=(nap,), sleep_pressure_nudge=nudge)
    assert schedule.has_conflicts is False
    assert len(schedule.timed_commitments) == 1
    assert [(s.start_minute, s.end_minute) for s in schedule.slices] == [
        (960, 1020),
        (1020, 1080),
        (1080, 1140),
    ]

    state = compute_now_next(schedule, 1200)
    assert state.sleep_pressure_nudge == nudge
