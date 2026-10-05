import unittest

from src.validator import validate_event


def base_event(**overrides):
    event = {
        "event_id": "e-1",
        "event_type": "SYMPTOM_RECORDED",
        "aggregate_type": "patient_episode",
        "aggregate_id": "ep-x",
        "occurred_at": "2026-10-04T01:20:00+08:00",
        "version": 1,
        "summary": "测试事件",
        "payload": {"symptoms": ["呕吐"], "onset_at": "2026-10-04T00:30:00+08:00"},
    }
    event.update(overrides)
    return event


class ValidatorTest(unittest.TestCase):
    def test_valid_event_passes(self) -> None:
        self.assertEqual(validate_event(base_event()), [])

    def test_missing_envelope_field(self) -> None:
        event = base_event()
        del event["event_id"]
        self.assertIn("缺少字段：event_id", validate_event(event))

    def test_unknown_event_type(self) -> None:
        errors = validate_event(base_event(event_type="SOMETHING_ELSE"))
        self.assertIn("未知事件类型：SOMETHING_ELSE", errors)

    def test_unknown_aggregate_type(self) -> None:
        errors = validate_event(base_event(aggregate_type="ward"))
        self.assertIn("未知聚合类型：ward", errors)

    def test_event_on_wrong_aggregate(self) -> None:
        event = base_event(
            event_type="SPECIMEN_COLLECTED",
            payload={"incident_id": "i-1", "kind": "leftover", "holder": "某院"},
        )
        errors = validate_event(event)
        self.assertIn("事件 SPECIMEN_COLLECTED 不应挂在聚合 patient_episode 上", errors)

    def test_missing_payload(self) -> None:
        event = base_event()
        del event["payload"]
        self.assertIn("事件 SYMPTOM_RECORDED 缺少 payload", validate_event(event))

    def test_missing_payload_key(self) -> None:
        event = base_event(payload={"symptoms": ["呕吐"]})
        self.assertIn("payload 缺少字段：onset_at", validate_event(event))

    def test_bad_version(self) -> None:
        self.assertIn("version 必须是正整数", validate_event(base_event(version=0)))


if __name__ == "__main__":
    unittest.main()
