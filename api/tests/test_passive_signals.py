"""Unit tests for Chronos passive habit signals, Kratos hydration, and peer gateways."""

from datetime import date
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import httpx

from app.domain.models import GridBlock, NudgeMode, SliceKind
from app.domain.passive_signals import (
    RULE_EIR_FASTED_WALK_40M,
    RULE_EIR_MORNING_WEIGH_IN,
    RULE_KRATOS_WORKOUT_COMPLETED,
    RULE_PLUTUS_MONTHLY_CHECKIN,
    EirDaySnapshot,
    EirDetectedWorkout,
    KratosDaySnapshot,
    KratosPlannedDay,
    KratosSessionSnapshot,
    PeerFetchStatus,
    PlutusAccountSnapshot,
    PlutusCheckinSnapshot,
    attach_workout_hydration_to_schedule,
    build_kratos_completed_commitments,
    evaluate_passive_rule,
    evaluate_passive_signals,
    evaluate_workout_hydration,
)
from app.domain.schedule_engine import resolve_day_schedule
from app.services.peer_gateways import HttpEirGateway, HttpKratosGateway, HttpPlutusGateway

TARGET_MONDAY = date(2026, 11, 23)
AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")


def _healthy_eir(*, weight_kg: object = "78.4", walk_minutes: float = 45) -> EirDaySnapshot:
    return EirDaySnapshot(
        status=PeerFetchStatus(available=True),
        # 2026-11-22T18:12:00Z == 2026-11-23T07:12:00+13:00 in Pacific/Auckland
        latest_measured_at="2026-11-22T18:12:00Z",
        latest_weight_kg=weight_kg,
        detected_workouts=(
            EirDetectedWorkout(
                started_at="2026-11-23T06:00:00+13:00",
                ended_at="2026-11-23T06:45:00+13:00",
                duration_minutes=walk_minutes,
                activity_type="walk",
                classification="walk",
            ),
        ),
    )


def _healthy_kratos() -> KratosDaySnapshot:
    return KratosDaySnapshot(
        status=PeerFetchStatus(available=True),
        planned_days=(
            KratosPlannedDay(
                day="Mon",
                day_index=0,
                session_name="Upper A",
                workout_type="strength",
                emphasis="Push & Pull",
                notes="Keep 2 RIR",
            ),
        ),
        sessions=(
            KratosSessionSnapshot(
                session_id="sess-1",
                routine_id=10,
                routine_name="Upper A",
                activity_type="strength",
                started_at="2026-11-23T17:30:00+13:00",
                ended_at="2026-11-23T18:20:00+13:00",
            ),
        ),
    )


def _healthy_plutus(
    *,
    days_ago_str: str = "2026-11-20T09:00:00+13:00",
    snapshot_date: str = "2026-11-20",
) -> PlutusCheckinSnapshot:
    return PlutusCheckinSnapshot(
        status=PeerFetchStatus(available=True),
        accounts=(
            PlutusAccountSnapshot(
                account_id="acc-1",
                name="Everyday Checking",
                last_synced_at=days_ago_str,
                latest_snapshot_date=snapshot_date,
            ),
        ),
        general_category_count=0,
        needs_review_count=0,
    )


def test_passive_signals_evaluate_all_four_canonical_rules() -> None:
    report = evaluate_passive_signals(
        target_date=TARGET_MONDAY,
        eir=_healthy_eir(weight_kg=Decimal("78.40"), walk_minutes=45),
        kratos=_healthy_kratos(),
        plutus=_healthy_plutus(),
        tz=AUCKLAND_TZ,
    )

    assert report.degraded_peers == ()
    assert set(report.signals.keys()) == {
        RULE_EIR_MORNING_WEIGH_IN,
        RULE_EIR_FASTED_WALK_40M,
        RULE_KRATOS_WORKOUT_COMPLETED,
        RULE_PLUTUS_MONTHLY_CHECKIN,
    }
    weigh_in = report.signals[RULE_EIR_MORNING_WEIGH_IN]
    assert weigh_in.observed is True
    assert weigh_in.fact_label == "Weighed in 07:12"
    assert weigh_in.peer_available is True

    walk = report.signals[RULE_EIR_FASTED_WALK_40M]
    assert walk.observed is True
    assert walk.fact_label == "Walk 45m observed"

    workout = report.signals[RULE_KRATOS_WORKOUT_COMPLETED]
    assert workout.observed is True
    assert workout.fact_label == "Upper A logged"

    plutus = report.signals[RULE_PLUTUS_MONTHLY_CHECKIN]
    assert plutus.observed is True
    assert plutus.fact_label == "Accounts fresh, queue clear"


def test_passive_signal_edge_cases_and_legacy_kratos_alias() -> None:
    # Walk < 40m is not observed, weigh-in from yesterday in Auckland is not observed
    eir = EirDaySnapshot(
        status=PeerFetchStatus(available=True),
        latest_measured_at="2026-11-22T23:55:00+13:00",
        latest_weight_kg="78.1",
        detected_workouts=(
            EirDetectedWorkout(
                started_at="2026-11-23T06:00:00+13:00",
                ended_at="2026-11-23T06:39:00+13:00",
                duration_minutes=39,
                classification="walk",
            ),
        ),
    )
    # Kratos session deleted + in-progress ignored; completed swim with no routine_name falls back
    kratos = KratosDaySnapshot(
        status=PeerFetchStatus(available=True),
        sessions=(
            KratosSessionSnapshot(
                session_id="deleted-1",
                routine_name="Deleted Lift",
                started_at="2026-11-23T08:00:00+13:00",
                ended_at="2026-11-23T09:00:00+13:00",
                deleted_at="2026-11-23T09:05:00+13:00",
            ),
            KratosSessionSnapshot(
                session_id="open-1",
                routine_name="Open Lift",
                started_at="2026-11-23T12:00:00+13:00",
                ended_at=None,
            ),
            KratosSessionSnapshot(
                session_id="swim-1",
                activity_type="swim",
                started_at="2026-11-23T17:00:00+13:00",
                ended_at="2026-11-23T17:40:00+13:00",
            ),
        ),
    )
    # Plutus stale or uncategorized General > 0 or needs_review > 0 -> unobserved
    plutus_stale = _healthy_plutus(
        days_ago_str="2026-10-10T09:00:00+13:00",
        snapshot_date="2026-10-10",
    )
    report = evaluate_passive_signals(
        target_date=TARGET_MONDAY, eir=eir, kratos=kratos, plutus=plutus_stale, tz=AUCKLAND_TZ
    )
    assert report.signals[RULE_EIR_MORNING_WEIGH_IN].observed is False
    assert report.signals[RULE_EIR_FASTED_WALK_40M].observed is False
    assert report.signals[RULE_KRATOS_WORKOUT_COMPLETED].observed is True
    assert report.signals[RULE_KRATOS_WORKOUT_COMPLETED].fact_label == "Swim logged"
    assert report.signals[RULE_PLUTUS_MONTHLY_CHECKIN].observed is False

    alias_result = evaluate_passive_rule(
        "kratos_workout_logged",
        target_date=TARGET_MONDAY,
        eir=eir,
        kratos=kratos,
        plutus=plutus_stale,
        tz=AUCKLAND_TZ,
    )
    assert alias_result.rule_key == RULE_KRATOS_WORKOUT_COMPLETED
    assert alias_result.observed is True
    assert alias_result.fact_label == "Swim logged"

    plutus_dirty = PlutusCheckinSnapshot(
        status=PeerFetchStatus(available=True),
        accounts=_healthy_plutus().accounts,
        general_category_count=1,
        needs_review_count=0,
    )
    assert (
        evaluate_passive_signals(
            target_date=TARGET_MONDAY, eir=eir, kratos=kratos, plutus=plutus_dirty, tz=AUCKLAND_TZ
        )
        .signals[RULE_PLUTUS_MONTHLY_CHECKIN]
        .observed
        is False
    )

    plutus_receipts = PlutusCheckinSnapshot(
        status=PeerFetchStatus(available=True),
        accounts=_healthy_plutus().accounts,
        general_category_count=0,
        needs_review_count=2,
    )
    assert (
        evaluate_passive_signals(
            target_date=TARGET_MONDAY, eir=eir, kratos=kratos, plutus=plutus_receipts, tz=AUCKLAND_TZ
        )
        .signals[RULE_PLUTUS_MONTHLY_CHECKIN]
        .observed
        is False
    )


def test_degraded_peers_emit_unobserved_without_error() -> None:
    report = evaluate_passive_signals(
        target_date=TARGET_MONDAY,
        eir=EirDaySnapshot(status=PeerFetchStatus(available=False, error="timeout")),
        kratos=KratosDaySnapshot(status=PeerFetchStatus(available=False, error="timeout")),
        plutus=PlutusCheckinSnapshot(status=PeerFetchStatus(available=False, error="timeout")),
        tz=AUCKLAND_TZ,
    )
    assert report.degraded_peers == ("eir", "kratos", "plutus")
    for signal in report.signals.values():
        assert signal.observed is False
        assert signal.fact_label is None
        assert signal.observed_at is None
        assert signal.peer_available is False


def test_kratos_hydration_grid_block_match_carve_out_and_day_header_fallback() -> None:
    workout_block = GridBlock(
        id=42,
        title="Morning Lift",
        day_of_week=0,
        start_minute=1020,  # 17:00
        end_minute=1140,    # 19:00
        category="routine",
        nudge_mode=NudgeMode.BOUNDARY,
    )
    kratos = _healthy_kratos()
    commitments, completed = build_kratos_completed_commitments(TARGET_MONDAY, kratos, tz=AUCKLAND_TZ)
    assert len(commitments) == 1
    assert commitments[0].id == -1
    assert commitments[0].read_only is True
    assert commitments[0].nudge_mode == NudgeMode.NONE

    schedule = resolve_day_schedule(TARGET_MONDAY, [workout_block], [], commitments)
    hydration = evaluate_workout_hydration(
        TARGET_MONDAY, kratos, schedule, completed_sessions=completed, tz=AUCKLAND_TZ
    )
    hydrated_schedule = attach_workout_hydration_to_schedule(schedule, hydration)

    assert hydrated_schedule.workout_hydration is not None
    assert hydrated_schedule.workout_hydration.planned_workout is not None
    assert hydrated_schedule.workout_hydration.planned_workout.placement == "grid_block"
    assert hydrated_schedule.workout_hydration.planned_workout.attached_grid_block_id == 42

    grid_slices = [s for s in hydrated_schedule.slices if s.kind == SliceKind.GRID_BLOCK]
    assert len(grid_slices) == 2
    assert grid_slices[0].start_minute == 1020
    assert grid_slices[0].end_minute == 1050
    assert grid_slices[0].hydrated_workout is not None
    assert grid_slices[0].hydrated_workout.title == "Upper A"

    kratos_slices = [s for s in hydrated_schedule.slices if s.kind == SliceKind.COMMITMENT]
    assert len(kratos_slices) == 1
    assert kratos_slices[0].read_only is True
    assert kratos_slices[0].source_id == -1

    # No matching workout block -> day_header placement; rest day -> planned_workout is None
    focus_block = GridBlock(
        id=7,
        title="Deep Work",
        day_of_week=0,
        start_minute=540,
        end_minute=660,
        category="focus",
    )
    no_workout_sched = resolve_day_schedule(TARGET_MONDAY, [focus_block], [], [])
    header_hydration = evaluate_workout_hydration(
        TARGET_MONDAY, kratos, no_workout_sched, completed_sessions=(), tz=AUCKLAND_TZ
    )
    assert header_hydration.planned_workout is not None
    assert header_hydration.planned_workout.placement == "day_header"
    assert header_hydration.planned_workout.attached_grid_block_id is None

    rest_kratos = KratosDaySnapshot(
        status=PeerFetchStatus(available=True),
        planned_days=(KratosPlannedDay(day_index=None, session_name="Rest Day", workout_type="rest"),),
    )
    rest_hydration = evaluate_workout_hydration(
        TARGET_MONDAY, rest_kratos, no_workout_sched, completed_sessions=(), tz=AUCKLAND_TZ
    )
    assert rest_hydration.planned_workout is None
    assert rest_hydration.peer_degraded is False


def test_http_gateways_handle_happy_paths_404_routines_and_degradation() -> None:
    def eir_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/biometrics/latest":
            return httpx.Response(200, json={"measured_at": "2026-11-23T07:05:00+13:00", "weight_kg": "77.2"})
        if request.url.path == "/biometrics/detected-workouts":
            return httpx.Response(
                200,
                json=[{"started_at": "2026-11-23T06:00:00+13:00", "duration_minutes": 42, "activity_type": "walk"}],
            )
        return httpx.Response(404)

    eir = HttpEirGateway(base_url="http://eir-api:8000", client=httpx.Client(transport=httpx.MockTransport(eir_handler), base_url="http://eir-api:8000")).fetch_day_snapshot(TARGET_MONDAY)
    assert eir.status.available is True
    assert eir.latest_weight_kg == "77.2"
    assert len(eir.detected_workouts) == 1

    # Kratos 404 on weekly plan is NOT degraded; routine lookup resolves missing routine_name
    def kratos_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/plans/week/2026-W48":
            return httpx.Response(404, json={"detail": "Not found"})
        if request.url.path == "/sessions":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "s-99",
                        "routine_id": 5,
                        "started_at": "2026-11-23T17:00:00+13:00",
                        "ended_at": "2026-11-23T17:50:00+13:00",
                        "deleted_at": None,
                        "extra": {},
                    }
                ],
            )
        if request.url.path == "/routines":
            return httpx.Response(200, json=[{"id": 5, "name": "Lower B"}])
        return httpx.Response(500)

    kratos = HttpKratosGateway(base_url="http://kratos-api:8000", client=httpx.Client(transport=httpx.MockTransport(kratos_handler), base_url="http://kratos-api:8000")).fetch_day_snapshot(TARGET_MONDAY)
    assert kratos.status.available is True
    assert kratos.planned_days == ()
    assert dict(kratos.routine_names_by_id) == {"5": "Lower B"}

    # Plutus missing X-Total-Count header degrades gracefully
    def plutus_bad_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/accounts":
            return httpx.Response(200, json=[{"id": 1, "name": "Main", "last_synced_at": "2026-11-22T09:00:00+13:00"}])
        if request.url.path == "/accounts/1/snapshots":
            return httpx.Response(200, json=[{"snapshot_date": "2026-11-22", "created_at": "2026-11-22T09:00:00+13:00"}])
        if request.url.path == "/transactions":
            return httpx.Response(200, json=[])  # Missing X-Total-Count
        return httpx.Response(200, json=[])

    plutus_bad = HttpPlutusGateway(base_url="http://plutus-api:8000", client=httpx.Client(transport=httpx.MockTransport(plutus_bad_handler), base_url="http://plutus-api:8000")).fetch_checkin_snapshot(TARGET_MONDAY)
    assert plutus_bad.status.available is False

    # Timeout degrades cleanly
    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    eir_timeout = HttpEirGateway(base_url="http://eir-api:8000", client=httpx.Client(transport=httpx.MockTransport(timeout_handler), base_url="http://eir-api:8000")).fetch_day_snapshot(TARGET_MONDAY)
    assert eir_timeout.status.available is False

    # Unset URL returns unavailable with zero network call
    assert HttpEirGateway(base_url="").fetch_day_snapshot(TARGET_MONDAY).status.available is False
    assert HttpKratosGateway(base_url="").fetch_day_snapshot(TARGET_MONDAY).status.available is False
    assert HttpPlutusGateway(base_url="").fetch_checkin_snapshot(TARGET_MONDAY).status.available is False
    assert json.dumps({"ok": True}) == '{"ok": true}'
