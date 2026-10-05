"""由事件流推导聚合状态：聚集事件、患者留观、样本链路与合并记录。

投影只追加不改写：检验更正与原始结果同时保留，合并以撤销事件还原，
与"业务更正应产生后继记录"的约定一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import EventStore


@dataclass
class LabResult:
    result_id: str
    analyte: str
    value: object
    unit: str | None
    measured_at: str
    facility: str | None
    corrects: str | None = None        # 本结果更正的原始结果编号
    reason: str | None = None
    superseded_by: str | None = None   # 取代本结果的更正结果编号


@dataclass
class PatientEpisode:
    episode_id: str
    first_event_at: str
    identity_status: str = "unconfirmed"   # unconfirmed | provisional | confirmed
    status: str = "open"                   # open | closed
    registered_facility: str | None = None
    symptoms: list[dict] = field(default_factory=list)
    labs: dict[str, LabResult] = field(default_factory=dict)
    lab_order: list[str] = field(default_factory=list)
    clues: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    transfers: list[dict] = field(default_factory=list)
    contact_attempts: list[dict] = field(default_factory=list)
    close_basis: dict | None = None
    merged_into: str | None = None

    def current_facility(self) -> str | None:
        if self.transfers:
            return self.transfers[-1].get("to_facility")
        return self.registered_facility

    def latest_lab(self, analyte: str) -> LabResult | None:
        """最新有效结果（被更正取代的原始结果不参与）。"""
        for result_id in reversed(self.lab_order):
            result = self.labs[result_id]
            if result.analyte == analyte.upper() and result.superseded_by is None:
                return result
        return None

    def lab_history(self, analyte: str) -> list[LabResult]:
        """原始结果与更正结果同时保留，按记录顺序返回。"""
        return [self.labs[rid] for rid in self.lab_order if self.labs[rid].analyte == analyte.upper()]


@dataclass
class DinerInfo:
    diner_ref: str
    link_basis: str
    episode_id: str | None = None
    contacted: bool = False
    attempts: list[dict] = field(default_factory=list)


@dataclass
class MealIncident:
    incident_id: str
    meal_batch: dict = field(default_factory=dict)
    collection_sites: list[dict] = field(default_factory=list)
    diners: dict[str, DinerInfo] = field(default_factory=dict)
    specimens: list[str] = field(default_factory=list)


@dataclass
class Specimen:
    specimen_id: str
    incident_id: str
    kind: str
    holder: str
    custody: list[dict] = field(default_factory=list)
    toxins: list[dict] = field(default_factory=list)


@dataclass
class MergeRecord:
    merge_event_id: str
    source_episode_id: str
    target_episode_id: str
    reason: str
    reverted: bool = False
    revert_reason: str | None = None


@dataclass
class DomainState:
    incidents: dict[str, MealIncident] = field(default_factory=dict)
    episodes: dict[str, PatientEpisode] = field(default_factory=dict)
    specimens: dict[str, Specimen] = field(default_factory=dict)
    merges: dict[str, MergeRecord] = field(default_factory=dict)
    followups: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)

    def resolve(self, episode_id: str) -> str:
        """沿有效合并链找到当前档案；撤销合并后自动还原。"""
        seen: set[str] = set()
        current = episode_id
        while current in self.episodes and self.episodes[current].merged_into and current not in seen:
            seen.add(current)
            current = self.episodes[current].merged_into
        return current

    def open_episodes(self) -> list[PatientEpisode]:
        return [ep for ep in self.episodes.values() if ep.status == "open" and not ep.merged_into]


def _incident(state: DomainState, incident_id: str) -> MealIncident:
    incident = state.incidents.get(incident_id)
    if incident is None:
        incident = MealIncident(incident_id)
        state.incidents[incident_id] = incident
    return incident


def _episode(state: DomainState, event: dict) -> PatientEpisode:
    episode_id = event["aggregate_id"]
    episode = state.episodes.get(episode_id)
    if episode is None:
        episode = PatientEpisode(episode_id=episode_id, first_event_at=event["occurred_at"])
        state.episodes[episode_id] = episode
    return episode


def _specimen(state: DomainState, specimen_id: str) -> Specimen | None:
    return state.specimens.get(specimen_id)


def _on_incident_registered(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    incident = _incident(state, event["aggregate_id"])
    incident.meal_batch = payload["meal_batch"]
    incident.collection_sites = list(payload.get("collection_sites", []))


def _on_diner_linked(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    incident = _incident(state, event["aggregate_id"])
    ref = payload["diner_ref"]
    diner = incident.diners.get(ref)
    if diner is None:
        diner = DinerInfo(diner_ref=ref, link_basis=payload["link_basis"])
        incident.diners[ref] = diner
    diner.link_basis = payload["link_basis"]
    if payload.get("episode_id"):
        diner.episode_id = payload["episode_id"]


def _on_contact_attempted(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    attempt = {
        "target": payload["target"],
        "channel": payload["channel"],
        "outcome": payload["outcome"],
        "at": event["occurred_at"],
    }
    if event["aggregate_type"] == "meal_incident":
        diner = _incident(state, event["aggregate_id"]).diners.get(payload["target"])
        if diner is not None:
            diner.attempts.append(attempt)
            if payload["outcome"] == "reached":
                diner.contacted = True
    else:
        _episode(state, event).contact_attempts.append(attempt)


def _on_specimen_collected(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    specimen = Specimen(
        specimen_id=event["aggregate_id"],
        incident_id=payload["incident_id"],
        kind=payload["kind"],
        holder=payload["holder"],
    )
    state.specimens[specimen.specimen_id] = specimen
    incident = _incident(state, payload["incident_id"])
    if specimen.specimen_id not in incident.specimens:
        incident.specimens.append(specimen.specimen_id)


def _on_specimen_transferred(state: DomainState, event: dict) -> None:
    specimen = _specimen(state, event["aggregate_id"])
    if specimen is None:
        return
    payload = event["payload"]
    specimen.custody.append(
        {
            "from_holder": payload["from_holder"],
            "to_holder": payload["to_holder"],
            "purpose": payload.get("purpose"),
            "at": event["occurred_at"],
        }
    )
    specimen.holder = payload["to_holder"]


def _on_toxin_identified(state: DomainState, event: dict) -> None:
    specimen = _specimen(state, event["aggregate_id"])
    if specimen is None:
        return
    payload = event["payload"]
    specimen.toxins.append(
        {"toxin_class": payload["toxin_class"], "method": payload["method"], "at": event["occurred_at"]}
    )


def _remember_facility(episode: PatientEpisode, payload: dict) -> None:
    if episode.registered_facility is None:
        episode.registered_facility = payload.get("facility") or payload.get("presenting_facility")


def _on_symptom(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    episode = _episode(state, event)
    episode.symptoms.append(
        {
            "symptoms": list(payload["symptoms"]),
            "onset_at": payload["onset_at"],
            "relieved_at": payload.get("relieved_at"),
            "facility": payload.get("facility"),
            "at": event["occurred_at"],
        }
    )
    _remember_facility(episode, payload)


def _on_lab_recorded(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    episode = _episode(state, event)
    result = LabResult(
        result_id=payload["result_id"],
        analyte=str(payload["analyte"]).upper(),
        value=payload["value"],
        unit=payload.get("unit"),
        measured_at=payload["measured_at"],
        facility=payload.get("facility"),
    )
    episode.labs[result.result_id] = result
    episode.lab_order.append(result.result_id)
    _remember_facility(episode, payload)


def _on_lab_corrected(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    episode = _episode(state, event)
    original = episode.labs.get(payload["corrects_result_id"])
    result = LabResult(
        result_id=payload["result_id"],
        analyte=str(payload["analyte"]).upper(),
        value=payload["value"],
        unit=payload.get("unit"),
        measured_at=payload["measured_at"],
        facility=payload.get("facility"),
        corrects=payload["corrects_result_id"],
        reason=payload["reason"],
    )
    episode.labs[result.result_id] = result
    episode.lab_order.append(result.result_id)
    if original is not None:
        original.superseded_by = result.result_id
    _remember_facility(episode, payload)


def _on_order(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    episode = _episode(state, event)
    episode.orders.append(
        {
            "order_kind": payload["order_kind"],
            "detail": payload.get("detail"),
            "issued_by": payload["issued_by"],
            "at": event["occurred_at"],
        }
    )
    _remember_facility(episode, payload)


def _on_transfer(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    _episode(state, event).transfers.append(
        {
            "from_facility": payload["from_facility"],
            "to_facility": payload["to_facility"],
            "bed_id": payload.get("bed_id"),
            "reason": payload.get("reason"),
            "at": event["occurred_at"],
        }
    )


def _on_clue(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    # 外观或 AI 识别只记作线索，不构成诊断结论。
    _episode(state, event).clues.append(
        {
            "clue_kind": payload["clue_kind"],
            "suspected_toxins": list(payload["suspected_toxins"]),
            "source": payload.get("source"),
            "at": event["occurred_at"],
        }
    )


def _on_provisional(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    episode = _episode(state, event)
    if episode.identity_status != "confirmed":
        episode.identity_status = "provisional"
    _remember_facility(episode, payload)


def _on_identity_confirmed(state: DomainState, event: dict) -> None:
    _episode(state, event).identity_status = "confirmed"


def _on_merged(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    state.merges[event["event_id"]] = MergeRecord(
        merge_event_id=event["event_id"],
        source_episode_id=payload["source_episode_id"],
        target_episode_id=payload["target_episode_id"],
        reason=payload["reason"],
    )
    source = state.episodes.get(payload["source_episode_id"])
    if source is not None:
        source.merged_into = payload["target_episode_id"]


def _on_merge_reverted(state: DomainState, event: dict) -> None:
    payload = event["payload"]
    record = state.merges.get(payload["merge_event_id"])
    if record is None or record.reverted:
        return
    record.reverted = True
    record.revert_reason = payload["reason"]
    source = state.episodes.get(record.source_episode_id)
    if source is not None:
        source.merged_into = None


def _on_followup_due(state: DomainState, event: dict) -> None:
    state.followups.append(event)


def _on_escalation(state: DomainState, event: dict) -> None:
    state.escalations.append(event)


def _on_closed(state: DomainState, event: dict) -> None:
    episode = _episode(state, event)
    episode.status = "closed"
    episode.close_basis = event["payload"]["basis"]


_HANDLERS = {
    "MEAL_INCIDENT_REGISTERED": _on_incident_registered,
    "DINER_LINKED": _on_diner_linked,
    "CONTACT_ATTEMPTED": _on_contact_attempted,
    "SPECIMEN_COLLECTED": _on_specimen_collected,
    "SPECIMEN_TRANSFERRED": _on_specimen_transferred,
    "TOXIN_IDENTIFIED": _on_toxin_identified,
    "SYMPTOM_RECORDED": _on_symptom,
    "LAB_RESULT_RECORDED": _on_lab_recorded,
    "LAB_RESULT_CORRECTED": _on_lab_corrected,
    "MEDICAL_ORDER_ISSUED": _on_order,
    "TRANSFER_ARRANGED": _on_transfer,
    "CLUE_RECORDED": _on_clue,
    "PROVISIONAL_IDENTITY_OPENED": _on_provisional,
    "IDENTITY_CONFIRMED": _on_identity_confirmed,
    "EPISODES_MERGED": _on_merged,
    "EPISODE_MERGE_REVERTED": _on_merge_reverted,
    "FOLLOWUP_DUE": _on_followup_due,
    "ESCALATION_RAISED": _on_escalation,
    "OBSERVATION_CLOSED": _on_closed,
}


def build_state(store: EventStore) -> DomainState:
    state = DomainState()
    for event in store.events():
        handler = _HANDLERS.get(event["event_type"])
        if handler is not None:
            handler(state, event)
    return state
