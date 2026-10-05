import unittest
from datetime import datetime, timedelta, timezone

from src.aggregates import build_state
from src.commands import (
    DomainError,
    arrange_transfer,
    attempt_contact,
    confirm_identity,
    correct_lab,
    link_diner,
    merge_episodes,
    open_provisional_episode,
    record_lab,
    record_symptoms,
    register_incident,
    revert_merge,
)
from src.store import EventStore

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=TZ8)


class AggregatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = EventStore()

    def test_lab_correction_keeps_original_and_latest(self) -> None:
        record_lab(self.store, NOW, episode_id="ep-1", result_id="r-1", analyte="ALT",
                   value=46, unit="U/L", measured_at="2026-10-04T05:30:00+08:00")
        correct_lab(self.store, NOW, episode_id="ep-1", corrects_result_id="r-1", result_id="r-2",
                    analyte="ALT", value=86, unit="U/L", measured_at="2026-10-04T07:05:00+08:00",
                    reason="原标本溶血，复测更正")
        episode = build_state(self.store).episodes["ep-1"]
        self.assertEqual(episode.latest_lab("ALT").value, 86)
        history = episode.lab_history("ALT")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].value, 46)
        self.assertEqual(history[0].superseded_by, "r-2")
        self.assertEqual(history[1].corrects, "r-1")

    def test_correction_requires_existing_original(self) -> None:
        record_lab(self.store, NOW, episode_id="ep-1", result_id="r-1", analyte="ALT",
                   value=46, measured_at="2026-10-04T05:30:00+08:00")
        with self.assertRaises(DomainError):
            correct_lab(self.store, NOW, episode_id="ep-1", corrects_result_id="r-x", result_id="r-2",
                        analyte="ALT", value=86, measured_at="2026-10-04T07:05:00+08:00", reason="复测")

    def _confirmed_episode(self, episode_id: str) -> None:
        record_symptoms(self.store, NOW, episode_id=episode_id, symptoms=["呕吐"],
                        onset_at="2026-10-04T00:30:00+08:00", facility="县医院")
        confirm_identity(self.store, NOW, episode_id=episode_id, confirmed_by="接诊台")

    def test_merge_and_revert_restores_both(self) -> None:
        open_provisional_episode(self.store, NOW, episode_id="ep-t", presenting_facility="区域中心")
        self._confirmed_episode("ep-c")
        merge_event = merge_episodes(self.store, NOW, source_episode_id="ep-t",
                                     target_episode_id="ep-c", reason="疑似同一人")
        state = build_state(self.store)
        self.assertEqual(state.episodes["ep-t"].merged_into, "ep-c")
        self.assertEqual(state.resolve("ep-t"), "ep-c")
        self.assertNotIn("ep-t", [ep.episode_id for ep in state.open_episodes()])

        revert_merge(self.store, NOW, merge_event_id=merge_event["event_id"], reason="家属确认非同一人")
        state = build_state(self.store)
        self.assertIsNone(state.episodes["ep-t"].merged_into)
        self.assertEqual(state.resolve("ep-t"), "ep-t")
        self.assertEqual(state.episodes["ep-t"].identity_status, "provisional")
        self.assertIn("ep-t", [ep.episode_id for ep in state.open_episodes()])

    def test_merge_rules(self) -> None:
        self._confirmed_episode("ep-a")
        self._confirmed_episode("ep-b")
        with self.assertRaisesRegex(DomainError, "已确认身份"):
            merge_episodes(self.store, NOW, source_episode_id="ep-a", target_episode_id="ep-b", reason="x")
        open_provisional_episode(self.store, NOW, episode_id="ep-t", presenting_facility="区域中心")
        record_symptoms(self.store, NOW, episode_id="ep-t2", symptoms=["腹痛"],
                        onset_at="2026-10-04T04:00:00+08:00", facility="区域中心")
        with self.assertRaisesRegex(DomainError, "已确认身份的档案"):
            merge_episodes(self.store, NOW, source_episode_id="ep-t", target_episode_id="ep-t2", reason="x")

    def test_revert_unknown_or_repeated_merge_rejected(self) -> None:
        open_provisional_episode(self.store, NOW, episode_id="ep-t", presenting_facility="区域中心")
        self._confirmed_episode("ep-c")
        merge_event = merge_episodes(self.store, NOW, source_episode_id="ep-t",
                                     target_episode_id="ep-c", reason="疑似同一人")
        with self.assertRaisesRegex(DomainError, "未找到合并记录"):
            revert_merge(self.store, NOW, merge_event_id="evt-不存在", reason="x")
        revert_merge(self.store, NOW, merge_event_id=merge_event["event_id"], reason="误并")
        with self.assertRaisesRegex(DomainError, "已被撤销"):
            revert_merge(self.store, NOW, merge_event_id=merge_event["event_id"], reason="再次撤销")

    def test_transfer_updates_current_facility(self) -> None:
        record_symptoms(self.store, NOW, episode_id="ep-1", symptoms=["恶心"],
                        onset_at="2026-10-04T03:00:00+08:00", facility="市一院")
        arrange_transfer(self.store, NOW, episode_id="ep-1", from_facility="市一院",
                         to_facility="区域中毒救治中心", bed_id="中毒中心-07")
        episode = build_state(self.store).episodes["ep-1"]
        self.assertEqual(episode.current_facility(), "区域中毒救治中心")
        self.assertEqual(episode.status, "open")  # 转院不结束留观

    def test_contact_attempt_marks_diner_reached(self) -> None:
        register_incident(self.store, NOW, incident_id="inc-1",
                          meal_batch={"venue": "某处", "meal_time": "2026-10-03T18:30:00+08:00"})
        link_diner(self.store, NOW, incident_id="inc-1", diner_ref="同餐者-丁", link_basis="reported")
        attempt_contact(self.store, NOW, aggregate_type="meal_incident", aggregate_id="inc-1",
                        target="同餐者-丁", channel="phone", outcome="no_answer")
        attempt_contact(self.store, NOW, aggregate_type="meal_incident", aggregate_id="inc-1",
                        target="同餐者-丁", channel="phone", outcome="reached")
        diner = build_state(self.store).incidents["inc-1"].diners["同餐者-丁"]
        self.assertTrue(diner.contacted)
        self.assertEqual(len(diner.attempts), 2)


if __name__ == "__main__":
    unittest.main()
