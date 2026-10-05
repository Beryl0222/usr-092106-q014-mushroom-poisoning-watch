import json
import unittest
from datetime import datetime, timedelta, timezone

from src.aggregates import build_state
from src.commands import (
    attempt_contact,
    collect_specimen,
    emit_scan_events,
    link_diner,
    record_symptoms,
    register_incident,
    transfer_specimen,
)
from src.store import EventStore
from src.views import build_cdc_summary, build_duty_view

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=TZ8)


class ViewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = EventStore()
        register_incident(self.store, NOW, incident_id="inc-1",
                          meal_batch={"venue": "山雨人家", "meal_time": "2026-10-01T18:00:00+08:00"},
                          collection_sites=[{"site_id": "s-1", "name": "后山采集点"}])
        link_diner(self.store, NOW, incident_id="inc-1", diner_ref="同餐者-甲",
                   link_basis="confirmed", episode_id="ep-1")
        link_diner(self.store, NOW, incident_id="inc-1", diner_ref="同餐者-乙", link_basis="reported")
        record_symptoms(self.store, NOW, episode_id="ep-1", symptoms=["呕吐"],
                        onset_at="2026-10-02T00:30:00+08:00", facility="县医院")
        register_incident(self.store, NOW, incident_id="inc-2",
                          meal_batch={"venue": "另一家", "meal_time": "2026-10-02T18:00:00+08:00"})
        link_diner(self.store, NOW, incident_id="inc-2", diner_ref="同餐者-丙",
                   link_basis="confirmed", episode_id="ep-2")
        record_symptoms(self.store, NOW, episode_id="ep-2", symptoms=["恶心"],
                        onset_at="2026-10-03T02:00:00+08:00", facility="市一院")
        self.state = build_state(self.store)

    def test_duty_view_sorted_by_danger_window(self) -> None:
        view = build_duty_view(self.state, NOW)
        ordered = [row["episode_id"] for row in view["episodes"]]
        self.assertEqual(ordered, ["ep-1", "ep-2"])  # 10-04T18:00 早于 10-05T18:00
        self.assertEqual(view["episodes"][0]["danger_until"], "2026-10-04T18:00:00+08:00")

    def test_uncontacted_diners_listed_until_reached(self) -> None:
        view = build_duty_view(self.state, NOW)
        self.assertEqual([d["diner_ref"] for d in view["uncontacted_diners"]], ["同餐者-乙"])
        attempt_contact(self.store, NOW, aggregate_type="meal_incident", aggregate_id="inc-1",
                        target="同餐者-乙", channel="phone", outcome="reached")
        view = build_duty_view(build_state(self.store), NOW)
        self.assertEqual(view["uncontacted_diners"], [])

    def test_specimen_whereabouts_follows_transfers(self) -> None:
        collect_specimen(self.store, NOW, specimen_id="sp-1", incident_id="inc-1",
                         kind="leftover", holder="县医院")
        transfer_specimen(self.store, NOW, specimen_id="sp-1",
                          from_holder="县医院", to_holder="区域中心毒理实验室")
        view = build_duty_view(build_state(self.store), NOW)
        specimen = view["specimens"][0]
        self.assertEqual(specimen["current_holder"], "区域中心毒理实验室")
        self.assertEqual(specimen["transfer_count"], 1)

    def test_escalation_for_missing_critical_rechecks(self) -> None:
        view = build_duty_view(self.state, NOW)
        levels = {e["episode_id"]: e["level"] for e in view["escalations"]}
        self.assertEqual(levels["ep-1"], "standard")   # 距窗口结束还有 30 小时
        near_close = datetime(2026, 10, 4, 10, 0, tzinfo=TZ8)
        view = build_duty_view(self.state, near_close)
        levels = {e["episode_id"]: e["level"] for e in view["escalations"]}
        self.assertEqual(levels["ep-1"], "urgent")     # 距窗口结束仅 8 小时
        self.assertEqual(levels["ep-2"], "standard")

    def test_emit_scan_events_is_idempotent(self) -> None:
        created = emit_scan_events(self.store, NOW)
        self.assertTrue(created)
        types = {event["event_type"] for event in created}
        self.assertIn("FOLLOWUP_DUE", types)
        self.assertIn("ESCALATION_RAISED", types)
        self.assertEqual(emit_scan_events(self.store, NOW), [])

    def test_cdc_summary_only_locations_and_counts(self) -> None:
        summary = build_cdc_summary(self.state, NOW)
        incident = next(i for i in summary["incidents"] if i["incident_id"] == "inc-1")
        self.assertEqual(
            set(incident),
            {"incident_id", "meal_venue", "meal_time", "collection_sites",
             "diners_total", "presented", "under_observation", "cleared"},
        )
        self.assertEqual(incident["diners_total"], 2)
        self.assertEqual(incident["presented"], 1)
        self.assertEqual(incident["under_observation"], 1)
        self.assertEqual(incident["collection_sites"], ["后山采集点"])
        dumped = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("diner_ref", dumped)
        self.assertNotIn("同餐者", dumped)


if __name__ == "__main__":
    unittest.main()
