"""留观计划推导：疑似毒素集合 → 危险窗口与关键复查日程。

计划完全由共享事件与毒素参考数据推导，任何一家医院读到相同事件
都会得到相同的留观安排，保证患者在不同医院获得一致处置。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .aggregates import DomainState, MealIncident, PatientEpisode
from .timeutil import parse
from .toxins import DEFAULT_CLASS, TOXIN_CLASSES


def incident_of(state: DomainState, episode_id: str) -> MealIncident | None:
    for incident in state.incidents.values():
        for diner in incident.diners.values():
            if diner.episode_id == episode_id:
                return incident
    return None


def suspected_toxins(state: DomainState, episode: PatientEpisode) -> set[str]:
    """疑似毒素 = 患者线索（外观/AI 识别）∪ 同起事件剩余样本的毒检结果。

    两者都只是留观依据：线索可能被推翻，毒检只覆盖已送检样本，
    因此一律取并集、按最严口径留观；完全没有信息时按未明毒素处理。
    """
    found: set[str] = set()
    for clue in episode.clues:
        found.update(clue["suspected_toxins"])
    incident = incident_of(state, episode.episode_id)
    if incident is not None:
        for specimen_id in incident.specimens:
            specimen = state.specimens.get(specimen_id)
            if specimen is not None:
                found.update(t["toxin_class"] for t in specimen.toxins)
    known = {t for t in found if t in TOXIN_CLASSES}
    return known or {DEFAULT_CLASS}


def exposure_at(state: DomainState, episode: PatientEpisode) -> datetime:
    incident = incident_of(state, episode.episode_id)
    if incident is not None:
        meal_time = incident.meal_batch.get("meal_time")
        if meal_time:
            return parse(meal_time)
    return parse(episode.first_event_at)


@dataclass
class RecheckStatus:
    analyte: str
    interval_hours: float
    first_due_at: datetime
    last_measured_at: datetime | None
    next_due_at: datetime
    measured_after_latency: bool   # 潜伏期之后是否已有有效检验（关键复查）
    overdue: bool


@dataclass
class ObservationPlan:
    episode_id: str
    suspected_toxins: list[str]
    exposure_at: datetime
    danger_until: datetime
    window_open: bool
    rechecks: list[RecheckStatus]

    @property
    def overdue_rechecks(self) -> list[RecheckStatus]:
        return [r for r in self.rechecks if r.overdue]

    @property
    def missing_critical(self) -> list[RecheckStatus]:
        return [r for r in self.rechecks if not r.measured_after_latency]


def build_plan(state: DomainState, episode: PatientEpisode, now: datetime) -> ObservationPlan:
    toxins = suspected_toxins(state, episode)
    exposure = exposure_at(state, episode)
    watch_hours = max(TOXIN_CLASSES[t]["watch_hours"] for t in toxins)
    danger_until = exposure + timedelta(hours=watch_hours)
    window_open = now < danger_until

    # 合并各类毒素的复查要求：同一指标取最短间隔、最早潜伏期。
    schedule: dict[str, tuple[float, float]] = {}
    for toxin in toxins:
        info = TOXIN_CLASSES[toxin]
        for analyte, interval in info["critical_labs"].items():
            current = schedule.get(analyte)
            if current is None:
                schedule[analyte] = (interval, info["latency_hours"])
            else:
                schedule[analyte] = (min(current[0], interval), min(current[1], info["latency_hours"]))

    rechecks: list[RecheckStatus] = []
    for analyte, (interval, latency) in sorted(schedule.items()):
        first_due = exposure + timedelta(hours=latency)
        measured = [
            r for r in episode.labs.values()
            if r.analyte == analyte and r.superseded_by is None
        ]
        measured_times = [parse(r.measured_at) for r in measured]
        last_measured = max(measured_times, default=None)
        next_due = last_measured + timedelta(hours=interval) if last_measured else first_due
        rechecks.append(
            RecheckStatus(
                analyte=analyte,
                interval_hours=interval,
                first_due_at=first_due,
                last_measured_at=last_measured,
                next_due_at=next_due,
                measured_after_latency=any(t >= first_due for t in measured_times),
                overdue=window_open and next_due < now,
            )
        )

    return ObservationPlan(
        episode_id=episode.episode_id,
        suspected_toxins=sorted(toxins),
        exposure_at=exposure,
        danger_until=danger_until,
        window_open=window_open,
        rechecks=rechecks,
    )
