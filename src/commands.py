"""命令侧：在领域不变量约束下产生事件。

普通登记类命令只负责构造合规事件；合并、撤销、检验更正、解除观察
四类命令承载关键不变量：
- 错误合并必须可撤销，撤销引用原合并事件；
- 检验更正必须指向本留观的原始结果，原始结果保留；
- 症状暂缓不能结束观察，解除必须回到医学依据。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from .aggregates import DomainState, build_state
from .plan import build_plan
from .store import EventStore
from .timeutil import iso
from .toxins import TOXIN_CLASSES
from .validator import validate_event


class DomainError(ValueError):
    """违反领域不变量。"""


def _emit(
    store: EventStore,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    now: datetime,
    payload: dict,
    summary: str,
    event_id: str | None = None,
) -> dict:
    event = {
        "event_id": event_id or f"evt-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "occurred_at": iso(now),
        "version": store.next_version(aggregate_type, aggregate_id),
        "summary": summary,
        "payload": payload,
    }
    errors = validate_event(event)
    if errors:
        raise DomainError("；".join(errors))
    store.receive(event)
    return event


# ---------- 聚集事件与样本链路 ----------

def register_incident(store, now, *, incident_id, meal_batch, collection_sites=(), summary=None):
    return _emit(
        store, event_type="MEAL_INCIDENT_REGISTERED", aggregate_type="meal_incident",
        aggregate_id=incident_id, now=now,
        payload={"meal_batch": meal_batch, "collection_sites": list(collection_sites)},
        summary=summary or f"登记聚集事件 {incident_id}",
    )


def link_diner(store, now, *, incident_id, diner_ref, link_basis, episode_id=None, summary=None):
    payload = {"diner_ref": diner_ref, "link_basis": link_basis}
    if episode_id:
        payload["episode_id"] = episode_id
    return _emit(
        store, event_type="DINER_LINKED", aggregate_type="meal_incident",
        aggregate_id=incident_id, now=now, payload=payload,
        summary=summary or f"关联同餐者 {diner_ref}",
    )


def collect_specimen(store, now, *, specimen_id, incident_id, kind, holder, summary=None):
    return _emit(
        store, event_type="SPECIMEN_COLLECTED", aggregate_type="specimen",
        aggregate_id=specimen_id, now=now,
        payload={"incident_id": incident_id, "kind": kind, "holder": holder},
        summary=summary or f"采集样本 {specimen_id}（{kind}）",
    )


def transfer_specimen(store, now, *, specimen_id, from_holder, to_holder, purpose=None, summary=None):
    payload = {"from_holder": from_holder, "to_holder": to_holder}
    if purpose:
        payload["purpose"] = purpose
    return _emit(
        store, event_type="SPECIMEN_TRANSFERRED", aggregate_type="specimen",
        aggregate_id=specimen_id, now=now, payload=payload,
        summary=summary or f"样本 {specimen_id} 由 {from_holder} 转至 {to_holder}",
    )


def identify_toxin(store, now, *, specimen_id, toxin_class, method, summary=None):
    return _emit(
        store, event_type="TOXIN_IDENTIFIED", aggregate_type="specimen",
        aggregate_id=specimen_id, now=now,
        payload={"toxin_class": toxin_class, "method": method},
        summary=summary or f"样本 {specimen_id} 检出 {toxin_class}",
    )


# ---------- 患者留观 ----------

def open_provisional_episode(store, now, *, episode_id, presenting_facility, descriptors=None, summary=None):
    payload = {"presenting_facility": presenting_facility}
    if descriptors:
        payload["descriptors"] = descriptors
    return _emit(
        store, event_type="PROVISIONAL_IDENTITY_OPENED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"为身份未确认者建立临时留观 {episode_id}",
    )


def confirm_identity(store, now, *, episode_id, confirmed_by, summary=None):
    return _emit(
        store, event_type="IDENTITY_CONFIRMED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload={"confirmed_by": confirmed_by},
        summary=summary or f"确认 {episode_id} 身份",
    )


def record_symptoms(store, now, *, episode_id, symptoms, onset_at, relieved_at=None, facility=None, summary=None):
    payload = {"symptoms": list(symptoms), "onset_at": onset_at}
    if relieved_at:
        payload["relieved_at"] = relieved_at
    if facility:
        payload["facility"] = facility
    return _emit(
        store, event_type="SYMPTOM_RECORDED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"记录 {episode_id} 症状",
    )


def record_clue(store, now, *, episode_id, clue_kind, suspected_toxins, source=None, summary=None):
    payload = {"clue_kind": clue_kind, "suspected_toxins": list(suspected_toxins)}
    if source:
        payload["source"] = source
    return _emit(
        store, event_type="CLUE_RECORDED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"记录 {episode_id} 识别线索（仅线索）",
    )


def record_lab(store, now, *, episode_id, result_id, analyte, value, measured_at, unit=None, facility=None, summary=None):
    state = build_state(store)
    episode = state.episodes.get(episode_id)
    if episode is not None and result_id in episode.labs:
        raise DomainError(f"检验结果编号已存在：{result_id}")
    payload = {"result_id": result_id, "analyte": analyte, "value": value, "measured_at": measured_at}
    if unit:
        payload["unit"] = unit
    if facility:
        payload["facility"] = facility
    return _emit(
        store, event_type="LAB_RESULT_RECORDED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"记录 {episode_id} 检验 {analyte}",
    )


def correct_lab(store, now, *, episode_id, corrects_result_id, result_id, analyte, value, measured_at, reason,
                unit=None, facility=None, summary=None):
    """检验更正：更正事件与原始结果同时保留，原始结果标记为被取代。"""
    state = build_state(store)
    episode = state.episodes.get(episode_id)
    if episode is None or corrects_result_id not in episode.labs:
        raise DomainError(f"原检验结果不存在：{corrects_result_id}")
    if result_id in episode.labs:
        raise DomainError(f"更正结果编号已存在：{result_id}")
    payload = {
        "corrects_result_id": corrects_result_id,
        "result_id": result_id,
        "analyte": analyte,
        "value": value,
        "measured_at": measured_at,
        "reason": reason,
    }
    if unit:
        payload["unit"] = unit
    if facility:
        payload["facility"] = facility
    return _emit(
        store, event_type="LAB_RESULT_CORRECTED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"更正检验 {corrects_result_id}：{reason}",
    )


def issue_order(store, now, *, episode_id, order_kind, issued_by, detail=None, facility=None, summary=None):
    payload = {"order_kind": order_kind, "issued_by": issued_by}
    if detail:
        payload["detail"] = detail
    if facility:
        payload["facility"] = facility
    return _emit(
        store, event_type="MEDICAL_ORDER_ISSUED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"{episode_id} 医嘱：{order_kind}",
    )


def arrange_transfer(store, now, *, episode_id, from_facility, to_facility, bed_id=None, reason=None, summary=None):
    payload = {"from_facility": from_facility, "to_facility": to_facility}
    if bed_id:
        payload["bed_id"] = bed_id
    if reason:
        payload["reason"] = reason
    return _emit(
        store, event_type="TRANSFER_ARRANGED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload=payload,
        summary=summary or f"{episode_id} 转院 {from_facility} → {to_facility}",
    )


def attempt_contact(store, now, *, aggregate_type, aggregate_id, target, channel, outcome, summary=None):
    return _emit(
        store, event_type="CONTACT_ATTEMPTED", aggregate_type=aggregate_type,
        aggregate_id=aggregate_id, now=now,
        payload={"target": target, "channel": channel, "outcome": outcome},
        summary=summary or f"联络 {target}：{outcome}",
    )


# ---------- 合并与撤销 ----------

def merge_episodes(store, now, *, source_episode_id, target_episode_id, reason, summary=None):
    """把临时档案并入已确认档案；合并本身也是事件，可被后继撤销。"""
    state = build_state(store)
    source = state.episodes.get(source_episode_id)
    target = state.episodes.get(target_episode_id)
    if source is None or target is None:
        raise DomainError("合并双方档案都必须存在")
    if source_episode_id == target_episode_id:
        raise DomainError("不能将档案并入自身")
    if source.identity_status == "confirmed":
        raise DomainError("已确认身份的档案不能并入其他档案")
    if target.identity_status != "confirmed":
        raise DomainError("合并目标必须是已确认身份的档案")
    if source.merged_into:
        raise DomainError("该档案已并入其他档案")
    if target.merged_into:
        raise DomainError("合并目标本身已被并入他处")
    if source.status != "open":
        raise DomainError("已解除留观的档案不能合并")
    return _emit(
        store, event_type="EPISODES_MERGED", aggregate_type="patient_episode",
        aggregate_id=target_episode_id, now=now,
        payload={
            "source_episode_id": source_episode_id,
            "target_episode_id": target_episode_id,
            "reason": reason,
        },
        summary=summary or f"合并 {source_episode_id} → {target_episode_id}",
    )


def revert_merge(store, now, *, merge_event_id, reason, summary=None):
    """撤销错误合并：引用原合并事件，双方档案恢复独立。"""
    state = build_state(store)
    record = state.merges.get(merge_event_id)
    if record is None:
        raise DomainError(f"未找到合并记录：{merge_event_id}")
    if record.reverted:
        raise DomainError("该合并已被撤销")
    return _emit(
        store, event_type="EPISODE_MERGE_REVERTED", aggregate_type="patient_episode",
        aggregate_id=record.target_episode_id, now=now,
        payload={
            "merge_event_id": merge_event_id,
            "source_episode_id": record.source_episode_id,
            "target_episode_id": record.target_episode_id,
            "reason": reason,
        },
        summary=summary or f"撤销合并 {merge_event_id}",
    )


# ---------- 解除观察 ----------

def close_episode(store, now, *, episode_id, physician, rationale, confirming_result_ids=(), summary=None):
    """解除留观：必须满足窗口与关键复查，并把医学依据写进事件。

    症状暂缓（假愈期）不构成解除条件；每次解除都能沿 basis 回到
    当时的疑似毒素、危险窗口与关键复查结果。
    """
    state = build_state(store)
    episode = state.episodes.get(episode_id)
    if episode is None:
        raise DomainError(f"留观不存在：{episode_id}")
    errors: list[str] = []
    if episode.status == "closed":
        errors.append("留观已解除，不能重复操作")
    if episode.merged_into:
        errors.append("该档案已并入其他档案，请在合并目标上解除")
    plan = build_plan(state, episode, now)
    if plan.window_open:
        labels = "、".join(TOXIN_CLASSES[t]["label"] for t in plan.suspected_toxins)
        errors.append(
            f"疑似毒素（{labels}）的潜伏窗尚未到期，症状缓解不等于安全（假愈期风险），"
            f"危险窗口至 {iso(plan.danger_until)}"
        )
    for recheck in plan.rechecks:
        if not recheck.measured_after_latency:
            errors.append(f"缺少关键复查：{recheck.analyte} 在潜伏期后尚无有效检验结果")
    confirming = list(confirming_result_ids)
    foreign = [rid for rid in confirming if rid not in episode.labs]
    if foreign:
        errors.append(f"医学依据引用了不属于本留观的检验：{foreign}")
    if plan.rechecks and not confirming:
        errors.append("解除观察必须引用关键复查结果作为医学依据")
    if not physician or not rationale:
        errors.append("解除观察必须记录医师与医学理由")
    if errors:
        raise DomainError("；".join(errors))
    basis = {
        "physician": physician,
        "rationale": rationale,
        "confirming_result_ids": confirming,
        "suspected_toxins": plan.suspected_toxins,
        "danger_until": iso(plan.danger_until),
    }
    return _emit(
        store, event_type="OBSERVATION_CLOSED", aggregate_type="patient_episode",
        aggregate_id=episode_id, now=now, payload={"basis": basis},
        summary=summary or f"解除 {episode_id} 留观（{physician}）",
    )


# ---------- 随访与升级扫描 ----------

def emit_scan_events(store: EventStore, now: datetime) -> list[dict]:
    """扫描未结留观：到期复查生成 FOLLOWUP_DUE，缺关键复查生成 ESCALATION_RAISED。

    事件 id 由内容决定，重复扫描幂等，适合各院各自运行后汇合。
    """
    created: list[dict] = []
    state = build_state(store)
    for episode in state.open_episodes():
        plan = build_plan(state, episode, now)
        plan_id = f"plan-{episode.episode_id}"
        for recheck in plan.rechecks:
            if plan.window_open and recheck.next_due_at <= now:
                event_id = f"due-{episode.episode_id}-{recheck.analyte}-{iso(recheck.next_due_at)}"
                if store.get(event_id):
                    continue
                created.append(_emit(
                    store, event_type="FOLLOWUP_DUE", aggregate_type="observation_plan",
                    aggregate_id=plan_id, now=now, event_id=event_id,
                    payload={
                        "episode_id": episode.episode_id,
                        "due_kind": "lab_recheck",
                        "analytes": [recheck.analyte],
                        "due_at": iso(recheck.next_due_at),
                    },
                    summary=f"{episode.episode_id} 的 {recheck.analyte} 复查到期",
                ))
        overdue = plan.overdue_rechecks
        missing = [r for r in plan.missing_critical if now >= r.first_due_at]
        if plan.window_open and (overdue or missing):
            analytes = sorted({r.analyte for r in overdue} | {r.analyte for r in missing})
            earliest = min(r.next_due_at for r in overdue + missing)
            level = "urgent" if plan.danger_until - now <= timedelta(hours=12) else "standard"
            event_id = f"esc-{episode.episode_id}-{iso(earliest)}"
            if store.get(event_id):
                continue
            created.append(_emit(
                store, event_type="ESCALATION_RAISED", aggregate_type="observation_plan",
                aggregate_id=plan_id, now=now, event_id=event_id,
                payload={
                    "episode_id": episode.episode_id,
                    "reason": "critical_recheck_overdue",
                    "level": level,
                    "missing_rechecks": analytes,
                    "due_at": iso(earliest),
                    "danger_until": iso(plan.danger_until),
                },
                summary=f"{episode.episode_id} 缺关键复查 {','.join(analytes)}，升级联络",
            ))
    return created
