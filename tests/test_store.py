import unittest

from src.store import EventConflictError, EventStore


def make_event(event_id="e-1", occurred_at="2026-10-04T01:00:00+08:00", summary="登记"):
    return {
        "event_id": event_id,
        "event_type": "DINER_LINKED",
        "aggregate_type": "meal_incident",
        "aggregate_id": "inc-1",
        "occurred_at": occurred_at,
        "version": 1,
        "summary": summary,
        "payload": {"diner_ref": "同餐者-甲", "link_basis": "reported"},
    }


class StoreTest(unittest.TestCase):
    def test_duplicate_receive_is_idempotent(self) -> None:
        store = EventStore()
        self.assertTrue(store.receive(make_event()))
        self.assertFalse(store.receive(make_event()))
        self.assertEqual(len(store), 1)

    def test_conflicting_event_id_rejected(self) -> None:
        store = EventStore()
        store.receive(make_event())
        with self.assertRaises(EventConflictError):
            store.receive(make_event(summary="被篡改的内容"))

    def test_events_sorted_by_occurred_at(self) -> None:
        store = EventStore()
        store.receive(make_event("e-2", "2026-10-04T09:00:00+08:00"))
        store.receive(make_event("e-1", "2026-10-04T01:00:00+08:00"))
        self.assertEqual([e["event_id"] for e in store.events()], ["e-1", "e-2"])

    def test_next_version_counts_per_aggregate(self) -> None:
        store = EventStore()
        store.receive(make_event("e-1"))
        store.receive(make_event("e-2", "2026-10-04T02:00:00+08:00"))
        self.assertEqual(store.next_version("meal_incident", "inc-1"), 3)
        self.assertEqual(store.next_version("meal_incident", "inc-2"), 1)


if __name__ == "__main__":
    unittest.main()
