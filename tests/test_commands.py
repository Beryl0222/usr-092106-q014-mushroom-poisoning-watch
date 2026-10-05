import unittest
from datetime import datetime, timedelta, timezone

from src.aggregates import build_state
from src.commands import (
    DomainError,
    close_episode,
    link_diner,
    record_lab,
    record_symptoms,
    register_incident,
)
from src.store import EventStore

TZ8 = timezone(timedelta(hours=8))
MEAL_TIME = "2026-10-01T18:00:00+08:00"
# 未明毒素：留观窗 72h，危险窗口至 2026-10-04T18:00；潜伏期 24h，关键复查须晚于 2026-10-02T18:00。
AFTER_WINDOW = datetime(2026, 10, 4, 20, 0, tzinfo=TZ8)
BEFORE_WINDOW = datetime(2026, 10, 3, 20, 0, tzinfo=TZ8)


def build_episode(store: EventStore, analytes=("ALT", "AST", "INR", "CREATININE")) -> None:
    t0 = datetime(2026, 10, 1, 19, 0, tzinfo=TZ8)
    register_incident(store, t0, incident_id="inc-1",
                      meal_batch={"venue": "山雨人家", "meal_time": MEAL_TIME})
    link_diner(store, t0, incident_id="inc-1", diner_ref="同餐者-甲",
               link_basis="confirmed", episode_id="ep-1")
    record_symptoms(store, t0, episode_id="ep-1", symptoms=["呕吐", "腹泻"],
                    onset_at="2026-10-02T00:30:00+08:00",
                    relieved_at="2026-10-02T08:00:00+08:00",  # 症状早已暂缓
                    facility="县医院")
    for analyte in analytes:
        record_lab(store, datetime(2026, 10, 3, 10, 0, tzinfo=TZ8),
                   episode_id="ep-1", result_id=f"r-{analyte}", analyte=analyte,
                   value=1.0, measured_at="2026-10-03T09:00:00+08:00")


class CloseEpisodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = EventStore()

    def test_symptom_relief_cannot_close_before_window(self) -> None:
        build_episode(self.store)
        with self.assertRaisesRegex(DomainError, "假愈期"):
            close_episode(self.store, BEFORE_WINDOW, episode_id="ep-1",
                          physician="医师甲", rationale="症状缓解",
                          confirming_result_ids=["r-ALT", "r-AST", "r-INR", "r-CREATININE"])

    def test_close_requires_all_critical_rechecks(self) -> None:
        build_episode(self.store, analytes=("ALT", "AST", "INR"))
        with self.assertRaisesRegex(DomainError, "缺少关键复查：CREATININE"):
            close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                          physician="医师甲", rationale="窗口已过",
                          confirming_result_ids=["r-ALT", "r-AST", "r-INR"])

    def test_close_rejects_foreign_result_ids(self) -> None:
        build_episode(self.store)
        with self.assertRaisesRegex(DomainError, "不属于本留观"):
            close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                          physician="医师甲", rationale="窗口已过",
                          confirming_result_ids=["r-ALT", "r-别人的结果"])

    def test_close_requires_physician_and_rationale(self) -> None:
        build_episode(self.store)
        with self.assertRaisesRegex(DomainError, "医师与医学理由"):
            close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                          physician="", rationale="",
                          confirming_result_ids=["r-ALT", "r-AST", "r-INR", "r-CREATININE"])

    def test_close_success_records_medical_basis(self) -> None:
        build_episode(self.store)
        event = close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                              physician="医师甲", rationale="窗口已过且关键复查未见异常",
                              confirming_result_ids=["r-ALT", "r-AST", "r-INR", "r-CREATININE"])
        basis = event["payload"]["basis"]
        self.assertEqual(basis["physician"], "医师甲")
        self.assertEqual(basis["suspected_toxins"], ["unknown"])
        self.assertEqual(basis["danger_until"], "2026-10-04T18:00:00+08:00")
        episode = build_state(self.store).episodes["ep-1"]
        self.assertEqual(episode.status, "closed")
        self.assertEqual(episode.close_basis["confirming_result_ids"],
                         ["r-ALT", "r-AST", "r-INR", "r-CREATININE"])

    def test_close_twice_rejected(self) -> None:
        build_episode(self.store)
        close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                      physician="医师甲", rationale="窗口已过且复查正常",
                      confirming_result_ids=["r-ALT", "r-AST", "r-INR", "r-CREATININE"])
        with self.assertRaisesRegex(DomainError, "已解除"):
            close_episode(self.store, AFTER_WINDOW, episode_id="ep-1",
                          physician="医师甲", rationale="重复操作",
                          confirming_result_ids=["r-ALT", "r-AST", "r-INR", "r-CREATININE"])


if __name__ == "__main__":
    unittest.main()
