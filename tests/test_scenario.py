import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.aggregates import build_state
from src.commands import emit_scan_events
from src.plan import build_plan
from src.store import EventStore
from src.timeutil import iso
from src.views import build_cdc_summary, build_duty_view

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=TZ8)
SCENARIO = Path(__file__).parents[1] / "data" / "autumn_rain_scenario.json"


class ScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = json.loads(SCENARIO.read_text(encoding="utf-8"))["events"]

    def setUp(self) -> None:
        self.store = EventStore()
        for entry in self.entries:
            self.store.receive(entry)
        self.state = build_state(self.store)

    def test_cross_hospital_resend_deduplicated(self) -> None:
        self.assertEqual(len(self.entries), 31)
        self.assertEqual(len(self.store), 30)  # 末条为 evt-20261004-007 的跨院重发

    def test_lab_correction_keeps_both_results(self) -> None:
        episode = self.state.episodes["ep-0001"]
        self.assertEqual(episode.latest_lab("ALT").value, 86)
        history = episode.lab_history("ALT")
        self.assertEqual([r.value for r in history], [46, 86])
        self.assertEqual(history[0].superseded_by, "r-0001-b")

    def test_erroneous_merge_reverted(self) -> None:
        ep2 = self.state.episodes["ep-0002"]
        ep3 = self.state.episodes["ep-0003"]
        self.assertIsNone(ep3.merged_into)
        self.assertEqual(ep3.identity_status, "confirmed")
        self.assertEqual(len(ep2.symptoms), 1)  # 临时患者的症状没有留在乙的档案里
        self.assertEqual(len(ep3.symptoms), 1)
        merge = self.state.merges["evt-20261004-024"]
        self.assertTrue(merge.reverted)

    def test_duty_view_flags_self_discharged_patient(self) -> None:
        view = build_duty_view(self.state, NOW)
        escalated = {e["episode_id"] for e in view["escalations"]}
        self.assertIn("ep-0001", escalated)  # 自行离院且 ALT 复查已逾期
        row = next(r for r in view["episodes"] if r["episode_id"] == "ep-0001")
        self.assertIn("ALT", row["overdue_rechecks"])
        self.assertEqual(row["last_contact"]["outcome"], "no_answer")
        uncontacted = {d["diner_ref"] for d in view["uncontacted_diners"]}
        self.assertEqual(uncontacted, {"同餐者-丁", "同餐者-戊"})

    def test_specimen_chain_and_toxin_drive_plan(self) -> None:
        view = build_duty_view(self.state, NOW)
        specimen = view["specimens"][0]
        self.assertEqual(specimen["current_holder"], "区域中毒救治中心毒理实验室")
        self.assertEqual(specimen["toxins_identified"], ["amatoxin"])
        plan = build_plan(self.state, self.state.episodes["ep-0001"], NOW)
        self.assertEqual(plan.suspected_toxins, ["amatoxin"])
        self.assertEqual(iso(plan.danger_until), "2026-10-07T18:30:00+08:00")

    def test_cdc_summary_counts_only(self) -> None:
        summary = build_cdc_summary(self.state, NOW)
        incident = summary["incidents"][0]
        self.assertEqual(incident["diners_total"], 5)
        self.assertEqual(incident["presented"], 3)
        self.assertEqual(incident["under_observation"], 3)
        self.assertEqual(incident["cleared"], 0)
        dumped = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("同餐者", dumped)

    def test_scan_events_materialize_and_dedup(self) -> None:
        created = emit_scan_events(self.store, NOW)
        types = {event["event_type"] for event in created}
        self.assertIn("FOLLOWUP_DUE", types)
        self.assertIn("ESCALATION_RAISED", types)
        escalated = {
            event["payload"]["episode_id"]
            for event in created
            if event["event_type"] == "ESCALATION_RAISED"
        }
        self.assertIn("ep-0001", escalated)
        self.assertEqual(emit_scan_events(self.store, NOW), [])


if __name__ == "__main__":
    unittest.main()
