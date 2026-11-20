import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from workspace_server import Conflict, ShiftRepository, valid_snapshot


class ShiftRepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = ShiftRepository(Path(directory.name) / "shifts.sqlite3")
        self.shift = {
            "id": "1" * 32,
            "version": 2,
            "title": "Пробная смена",
            "createdAt": "2026-11-19T00:00:00Z",
            "status": "open",
            "vehicleIds": ["КМ-01"],
            "jobs": [{"id": "З-001", "state": "active"}],
            "events": [{"message": "Начало"}],
        }

    def test_only_one_client_can_commit_the_same_revision(self):
        self.repo.write(self.shift, 0)

        def update(title):
            state = {**self.shift, "title": title}
            try:
                self.repo.write(state, 1)
                return title
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["Клиент А", "Клиент Б"]))

        self.assertEqual(results.count(None), 1)
        self.assertEqual(self.repo.read(self.shift["id"])["revision"], 2)
        self.assertIn(self.repo.list()[0]["title"], results)

    def test_journal_preserved_and_closed_shift_is_immutable(self):
        self.repo.write(self.shift, 0)
        with self.assertRaises(Conflict):
            self.repo.write({**self.shift, "events": []}, 1)
        archived = {
            **self.shift, "status": "closed", "closedAt": "2026-11-19T01:00:00Z",
            "events": [{"message": "Смена закрыта"}, *self.shift["events"]],
            "jobs": [{"id": "З-001", "state": "accepted"}],
        }
        self.assertTrue(valid_snapshot(archived, archived["id"]))
        self.assertFalse(valid_snapshot({**archived, "jobs": self.shift["jobs"]}, archived["id"]))
        self.repo.write(archived, 1)
        with self.assertRaises(Conflict):
            self.repo.write(self.shift, 2)
        self.assertEqual(self.repo.read(self.shift["id"])["snapshot"], archived)


if __name__ == "__main__":
    unittest.main()
