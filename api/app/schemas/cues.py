"""Pydantic v2 request and response schemas for habit cues and friction reflection."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    CueReflectionGroup,
    FrictionNote,
    FrictionTag,
    HabitCue,
    TagReflectionGroup,
    WeeklyReflection,
)
from app.domain.passive_signals import PassiveSignalsReport


class FrictionNoteCreate(BaseModel):
    """Request body for recording a friction reflection note."""

    target_date: date
    tag: FrictionTag
    note: str
    habit_cue_id: int | None = None
    grid_block_id: int | None = None


class FrictionNoteResponse(BaseModel):
    """Serialized friction reflection note."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    target_date: date
    tag: FrictionTag
    note: str
    habit_cue_id: int | None = None
    grid_block_id: int | None = None
    cue_title: str | None = None
    block_title: str | None = None
    created_at: datetime

    @classmethod
    def from_domain(cls, item: FrictionNote) -> "FrictionNoteResponse":
        return cls.model_validate(item)


class HabitCueCreate(BaseModel):
    """Request body for creating a habit cue."""

    title: str
    zone_chain: str
    chain_order: int = 0
    passive_rule: str | None = None
    active: bool = True
    notes: str | None = None
    grid_block_ids: list[int] = Field(default_factory=list)


class HabitCueUpdate(BaseModel):
    """Partial update body for an existing habit cue."""

    title: str | None = None
    zone_chain: str | None = None
    chain_order: int | None = None
    passive_rule: str | None = None
    active: bool | None = None
    notes: str | None = None
    grid_block_ids: list[int] | None = None


class HabitCueResponse(BaseModel):
    """Serialized habit cue with anchored grid block IDs and recent friction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    zone_chain: str
    chain_order: int
    passive_rule: str | None = None
    active: bool
    notes: str | None = None
    grid_block_ids: list[int] = Field(default_factory=list)
    recent_friction: list[FrictionNoteResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, cue: HabitCue) -> "HabitCueResponse":
        return cls.model_validate(cue)


class TagReflectionGroupResponse(BaseModel):
    """Serialized friction reflection group keyed by FrictionTag."""

    model_config = ConfigDict(from_attributes=True)

    tag: FrictionTag
    count: int
    notes: list[FrictionNoteResponse]

    @classmethod
    def from_domain(cls, group: TagReflectionGroup) -> "TagReflectionGroupResponse":
        return cls.model_validate(group)


class CueReflectionGroupResponse(BaseModel):
    """Serialized friction reflection group keyed by HabitCue."""

    model_config = ConfigDict(from_attributes=True)

    habit_cue_id: int
    cue_title: str
    zone_chain: str
    count: int
    notes: list[FrictionNoteResponse]

    @classmethod
    def from_domain(cls, group: CueReflectionGroup) -> "CueReflectionGroupResponse":
        return cls.model_validate(group)


class WeeklyReflectionResponse(BaseModel):
    """Serialized 7-day weekly friction reflection."""

    model_config = ConfigDict(from_attributes=True)

    week_start: date
    week_end: date
    total_notes: int
    by_tag: list[TagReflectionGroupResponse]
    by_cue: list[CueReflectionGroupResponse]
    unlinked_notes: list[FrictionNoteResponse]

    @classmethod
    def from_domain(cls, reflection: WeeklyReflection) -> "WeeklyReflectionResponse":
        return cls.model_validate(reflection)


class PassiveSignalResponse(BaseModel):
    """Serialized passive signal evaluation state for one canonical rule."""

    model_config = ConfigDict(from_attributes=True)

    rule_key: str
    label: str
    observed: bool
    fact_label: str | None = None
    observed_at: str | None = None
    source: Literal["eir", "kratos", "plutus"]
    peer_available: bool


class PassiveSignalsResponse(BaseModel):
    """Serialized passive habit signals response across Eir, Kratos, and Plutus."""

    model_config = ConfigDict(from_attributes=True)

    target_date: date
    signals: dict[str, PassiveSignalResponse]
    degraded_peers: list[str]

    @classmethod
    def from_domain(cls, report: PassiveSignalsReport) -> "PassiveSignalsResponse":
        return cls.model_validate(report)
