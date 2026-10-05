"""校验领域事件信封与各事件类型的必备负载。"""

ENVELOPE_REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

# 兼容旧引用
REQUIRED = ENVELOPE_REQUIRED

EVENT_TYPES = (
    "MEAL_INCIDENT_REGISTERED",
    "DINER_LINKED",
    "SPECIMEN_COLLECTED",
    "SPECIMEN_TRANSFERRED",
    "TOXIN_IDENTIFIED",
    "CLUE_RECORDED",
    "SYMPTOM_RECORDED",
    "LAB_RESULT_RECORDED",
    "LAB_RESULT_CORRECTED",
    "MEDICAL_ORDER_ISSUED",
    "TRANSFER_ARRANGED",
    "CONTACT_ATTEMPTED",
    "PROVISIONAL_IDENTITY_OPENED",
    "IDENTITY_CONFIRMED",
    "EPISODES_MERGED",
    "EPISODE_MERGE_REVERTED",
    "FOLLOWUP_DUE",
    "ESCALATION_RAISED",
    "OBSERVATION_CLOSED",
)

AGGREGATE_TYPES = ("meal_incident", "patient_episode", "specimen", "observation_plan")

# 每类聚合允许承载的事件；跨院消息据此拒绝挂错对象的数据。
AGGREGATE_EVENTS = {
    "meal_incident": {"MEAL_INCIDENT_REGISTERED", "DINER_LINKED", "CONTACT_ATTEMPTED"},
    "patient_episode": {
        "SYMPTOM_RECORDED",
        "LAB_RESULT_RECORDED",
        "LAB_RESULT_CORRECTED",
        "MEDICAL_ORDER_ISSUED",
        "TRANSFER_ARRANGED",
        "CONTACT_ATTEMPTED",
        "CLUE_RECORDED",
        "PROVISIONAL_IDENTITY_OPENED",
        "IDENTITY_CONFIRMED",
        "EPISODES_MERGED",
        "EPISODE_MERGE_REVERTED",
        "OBSERVATION_CLOSED",
    },
    "specimen": {"SPECIMEN_COLLECTED", "SPECIMEN_TRANSFERRED", "TOXIN_IDENTIFIED"},
    "observation_plan": {"FOLLOWUP_DUE", "ESCALATION_RAISED"},
}

PAYLOAD_REQUIRED = {
    "MEAL_INCIDENT_REGISTERED": ("meal_batch",),
    "DINER_LINKED": ("diner_ref", "link_basis"),
    "SPECIMEN_COLLECTED": ("incident_id", "kind", "holder"),
    "SPECIMEN_TRANSFERRED": ("from_holder", "to_holder"),
    "TOXIN_IDENTIFIED": ("toxin_class", "method"),
    "CLUE_RECORDED": ("clue_kind", "suspected_toxins"),
    "SYMPTOM_RECORDED": ("symptoms", "onset_at"),
    "LAB_RESULT_RECORDED": ("result_id", "analyte", "value", "measured_at"),
    "LAB_RESULT_CORRECTED": ("corrects_result_id", "result_id", "analyte", "value", "measured_at", "reason"),
    "MEDICAL_ORDER_ISSUED": ("order_kind", "issued_by"),
    "TRANSFER_ARRANGED": ("from_facility", "to_facility"),
    "CONTACT_ATTEMPTED": ("target", "channel", "outcome"),
    "PROVISIONAL_IDENTITY_OPENED": ("presenting_facility",),
    "IDENTITY_CONFIRMED": ("confirmed_by",),
    "EPISODES_MERGED": ("source_episode_id", "target_episode_id", "reason"),
    "EPISODE_MERGE_REVERTED": ("merge_event_id", "reason"),
    "FOLLOWUP_DUE": ("episode_id", "due_kind", "due_at"),
    "ESCALATION_RAISED": ("episode_id", "reason"),
    "OBSERVATION_CLOSED": ("basis",),
}


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    event_type = record.get("event_type")
    aggregate_type = record.get("aggregate_type")
    if event_type is not None and event_type not in EVENT_TYPES:
        errors.append(f"未知事件类型：{event_type}")
    if aggregate_type is not None and aggregate_type not in AGGREGATE_TYPES:
        errors.append(f"未知聚合类型：{aggregate_type}")
    if event_type in EVENT_TYPES and aggregate_type in AGGREGATE_TYPES:
        if event_type not in AGGREGATE_EVENTS[aggregate_type]:
            errors.append(f"事件 {event_type} 不应挂在聚合 {aggregate_type} 上")
    required_payload = PAYLOAD_REQUIRED.get(event_type, ())
    if required_payload:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            errors.append(f"事件 {event_type} 缺少 payload")
        else:
            errors.extend(f"payload 缺少字段：{key}" for key in required_payload if key not in payload)
    return errors
