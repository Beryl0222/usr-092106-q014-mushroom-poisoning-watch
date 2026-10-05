"""值班视图与疾控汇总。

值班视图按危险窗口排序，集中呈现样本去向、尚未联系的同餐者与缺失复查；
疾控汇总只保留必要地点与人数，不含个人身份与诊疗细节。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .aggregates import DomainState
from .plan import build_plan
from .timeutil import iso
from .toxins import TOXIN_CLASSES

URGENT_WITHIN = timedelta(hours=12)


def scan_escalations(state: DomainState, now: datetime) -> list[dict]:
    """潜伏窗到期前缺关键复查的留观，列入升级联络。"""
    results: list[dict] = []
    for episode in state.open_episodes():
        plan = build_plan(state, episode, now)
        overdue = plan.overdue_rechecks
        missing = [r for r in plan.missing_critical if now >= r.first_due_at]
        if plan.window_open and (overdue or missing):
            earliest = min(r.next_due_at for r in overdue + missing)
            results.append(
                {
                    "episode_id": episode.episode_id,
                    "level": "urgent" if plan.danger_until - now <= URGENT_WITHIN else "standard",
                    "missing_rechecks": sorted({r.analyte for r in overdue} | {r.analyte for r in missing}),
                    "due_at": iso(earliest),
                    "danger_until": iso(plan.danger_until),
                }
            )
    return results


def build_duty_view(state: DomainState, now: datetime) -> dict:
    rows = []
    for episode in state.open_episodes():
        plan = build_plan(state, episode, now)
        next_due = min((r.next_due_at for r in plan.rechecks), default=None)
        rows.append(
            {
                "episode_id": episode.episode_id,
                "identity_status": episode.identity_status,
                "facility": episode.current_facility(),
                "suspected_toxins": [TOXIN_CLASSES[t]["label"] for t in plan.suspected_toxins],
                "danger_until": iso(plan.danger_until),
                "minutes_to_window_end": int((plan.danger_until - now).total_seconds() // 60),
                "overdue_rechecks": [r.analyte for r in plan.overdue_rechecks],
                "missing_critical": [r.analyte for r in plan.missing_critical],
                "next_recheck_due": iso(next_due) if next_due else None,
                "last_contact": episode.contact_attempts[-1] if episode.contact_attempts else None,
            }
        )
    rows.sort(key=lambda row: (row["danger_until"], row["episode_id"]))

    uncontacted = []
    for incident in state.incidents.values():
        for diner in incident.diners.values():
            if diner.episode_id is None and not diner.contacted:
                uncontacted.append(
                    {
                        "incident_id": incident.incident_id,
                        "diner_ref": diner.diner_ref,
                        "link_basis": diner.link_basis,
                        "attempts": len(diner.attempts),
                        "last_attempt_at": diner.attempts[-1]["at"] if diner.attempts else None,
                    }
                )

    specimens = [
        {
            "specimen_id": specimen.specimen_id,
            "incident_id": specimen.incident_id,
            "kind": specimen.kind,
            "current_holder": specimen.holder,
            "transfer_count": len(specimen.custody),
            "toxins_identified": [t["toxin_class"] for t in specimen.toxins],
        }
        for specimen in state.specimens.values()
    ]

    return {
        "generated_at": iso(now),
        "episodes": rows,
        "uncontacted_diners": uncontacted,
        "specimens": specimens,
        "escalations": scan_escalations(state, now),
    }


def build_cdc_summary(state: DomainState, now: datetime) -> dict:
    """疾控汇总：只汇总必要地点与人数。"""
    incidents = []
    for incident in state.incidents.values():
        resolved = {
            state.resolve(diner.episode_id)
            for diner in incident.diners.values()
            if diner.episode_id
        }
        resolved = {eid for eid in resolved if eid in state.episodes}
        incidents.append(
            {
                "incident_id": incident.incident_id,
                "meal_venue": incident.meal_batch.get("venue"),
                "meal_time": incident.meal_batch.get("meal_time"),
                "collection_sites": [site.get("name") for site in incident.collection_sites],
                "diners_total": len(incident.diners),
                "presented": len(resolved),
                "under_observation": sum(1 for eid in resolved if state.episodes[eid].status == "open"),
                "cleared": sum(1 for eid in resolved if state.episodes[eid].status == "closed"),
            }
        )
    return {"generated_at": iso(now), "incidents": incidents}
