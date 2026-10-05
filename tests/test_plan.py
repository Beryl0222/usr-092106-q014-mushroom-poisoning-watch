import unittest
from datetime import datetime, timedelta, timezone

from src.aggregates import build_state
from src.commands import (
    collect_specimen,
    identify_toxin,
    link_diner,
    record_clue,
    record_lab,
    record_symptoms,
    register_incident,
)
from src.plan import build_plan
from src.store import EventStore
from src.timeutil import parse

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 3, 13, 0, tzinfo=TZ8)
MEAL_TIME = "2026-10-01T18:00:00+08:00"


def fresh_episode(store: EventStore, episode_id: str = "ep-1") -> None:
    register_incident(store, NOW, incident_id="inc-1",
                      meal_batch={"venue": "山雨人家", "meal_time": MEAL_TIME})
    link_diner(store, NOW, incident_id="inc-1", diner_ref="同餐者-甲",
               link_basis="confirmed", episode_id=episode_id)
    record_symptoms(store, NOW, episode_id=episode_id, symptoms=["呕吐"],
                    onset_at="2026-10-02T00:30:00+08:00", facility="县医院")


def plan_for(store: EventStore, episode_id: str = "ep-1", now: datetime = NOW):
    state = build_state(store)
    return build_plan(state, state.episodes[episode_id], now)


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = EventStore()

    def test_unknown_toxin_default_window(self) -> None:
        fresh_episode(self.store)
        plan = plan_for(self.store)
        self.assertEqual(plan.suspected_toxins, ["unknown"])
        self.assertEqual(plan.danger_until, parse(MEAL_TIME) + timedelta(hours=72))

    def test_clue_extends_window_to_amatoxin(self) -> None:
        fresh_episode(self.store)
        record_clue(self.store, NOW, episode_id="ep-1", clue_kind="ai_image",
                    suspected_toxins=["amatoxin"], source="手机识图应用")
        plan = plan_for(self.store)
        self.assertEqual(plan.suspected_toxins, ["amatoxin"])
        self.assertEqual(plan.danger_until, parse(MEAL_TIME) + timedelta(hours=96))

    def test_specimen_toxin_applies_to_incident_episode(self) -> None:
        fresh_episode(self.store)
        collect_specimen(self.store, NOW, specimen_id="sp-1", incident_id="inc-1",
                         kind="leftover", holder="县医院")
        identify_toxin(self.store, NOW, specimen_id="sp-1", toxin_class="amatoxin",
                       method="液相色谱-串联质谱")
        plan = plan_for(self.store)
        self.assertEqual(plan.suspected_toxins, ["amatoxin"])

    def test_recheck_schedule_and_overdue(self) -> None:
        fresh_episode(self.store)
        record_lab(self.store, NOW, episode_id="ep-1", result_id="r-1", analyte="ALT",
                   value=40, measured_at="2026-10-03T00:00:00+08:00")
        plan = plan_for(self.store)
        alt = next(r for r in plan.rechecks if r.analyte == "ALT")
        self.assertEqual(alt.first_due_at, parse(MEAL_TIME) + timedelta(hours=24))
        self.assertEqual(alt.next_due_at, parse("2026-10-03T00:00:00+08:00") + timedelta(hours=12))
        self.assertTrue(alt.overdue)  # NOW 晚于 next_due
        self.assertTrue(alt.measured_after_latency)
        inr = next(r for r in plan.rechecks if r.analyte == "INR")
        self.assertFalse(inr.measured_after_latency)
        self.assertTrue(inr.overdue)

    def test_plan_is_order_independent(self) -> None:
        fresh_episode(self.store)
        record_clue(self.store, NOW, episode_id="ep-1", clue_kind="visual_description",
                    suspected_toxins=["amatoxin"])
        record_lab(self.store, NOW, episode_id="ep-1", result_id="r-1", analyte="ALT",
                   value=40, measured_at="2026-10-03T00:00:00+08:00")
        events = self.store.events()
        other = EventStore()
        for event in reversed(events):
            other.receive(event)
        first = plan_for(self.store)
        second = plan_for(other)
        self.assertEqual(first.danger_until, second.danger_until)
        self.assertEqual(first.suspected_toxins, second.suspected_toxins)
        self.assertEqual(
            [(r.analyte, r.next_due_at) for r in first.rechecks],
            [(r.analyte, r.next_due_at) for r in second.rechecks],
        )


if __name__ == "__main__":
    unittest.main()
