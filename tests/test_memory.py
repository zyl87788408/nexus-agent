"""Long-term memory: CRUD, retrieval, persistence, backup and restore."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.memory.store import MemoryStore


class MemoryCrudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "memory.db"
        self.store = MemoryStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_and_get(self):
        memory_id = self.store.add("user prefers concise answers",
                                   kind="preference", tags=["style"])
        record = self.store.get(memory_id)
        self.assertEqual(record["content"], "user prefers concise answers")
        self.assertEqual(record["kind"], "preference")
        self.assertEqual(record["tags"], ["style"])

    def test_empty_content_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add("   ")

    def test_update_changes_fields(self):
        memory_id = self.store.add("old text")
        self.assertTrue(self.store.update(memory_id, content="new text",
                                          importance=0.9))
        record = self.store.get(memory_id)
        self.assertEqual(record["content"], "new text")
        self.assertAlmostEqual(record["importance"], 0.9)

    def test_update_missing_returns_false(self):
        self.assertFalse(self.store.update("nonexistent", content="x"))

    def test_delete(self):
        memory_id = self.store.add("temporary")
        self.assertTrue(self.store.delete(memory_id))
        self.assertIsNone(self.store.get(memory_id))
        self.assertFalse(self.store.delete(memory_id))

    def test_count_tracks_entries(self):
        self.assertEqual(self.store.count(), 0)
        self.store.add("one")
        self.store.add("two")
        self.assertEqual(self.store.count(), 2)


class MemorySearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "memory.db")
        self.store.add("project uses FastAPI for the server", kind="fact")
        self.store.add("user prefers Chinese responses", kind="preference")
        self.store.add("deployment targets a 4GB VPS", kind="fact")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_keyword_search_finds_match(self):
        results = self.store.search("FastAPI")
        self.assertEqual(len(results), 1)
        self.assertIn("FastAPI", results[0]["content"])

    def test_search_by_kind_filters(self):
        results = self.store.search("prefers", kind="preference")
        self.assertTrue(results)
        self.assertTrue(all(r["kind"] == "preference" for r in results))

    def test_search_returns_empty_for_no_match(self):
        self.assertEqual(self.store.search("kubernetes"), [])

    def test_search_with_empty_query_is_empty(self):
        self.assertEqual(self.store.search("   "), [])

    def test_search_respects_limit(self):
        self.assertLessEqual(len(self.store.search("the", limit=1)), 1)

    def test_fts_operators_are_neutralised(self):
        # A raw FTS query would raise; quoting prevents that.
        self.assertEqual(self.store.search('NEAR("a" "b") OR'), [])

    def test_recent_orders_by_update(self):
        self.store.add("newest entry")
        recent = self.store.recent(limit=1)
        self.assertEqual(recent[0]["content"], "newest entry")

    def test_context_for_renders_hits(self):
        context = self.store.context_for("FastAPI")
        self.assertIn("FastAPI", context)


class MemoryPersistenceTest(unittest.TestCase):
    def test_survives_reopening(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "memory.db"
            first = MemoryStore(path)
            first.add("persisted fact")
            first.close()

            second = MemoryStore(path)
            try:
                self.assertEqual(second.count(), 1)
                self.assertEqual(second.recent()[0]["content"], "persisted fact")
            finally:
                second.close()

    def test_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "memory.db"
            store = MemoryStore(path)
            archive = store.add("precious data")
            backup_path = store.backup(Path(d) / "backup.db")
            store.delete(archive)
            self.assertEqual(store.count(), 0)
            store.close()

            restored = MemoryStore.restore(backup_path, Path(d) / "restored.db")
            try:
                self.assertEqual(restored.count(), 1)
                self.assertEqual(restored.recent()[0]["content"], "precious data")
            finally:
                restored.close()

    def test_restore_missing_backup_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                MemoryStore.restore(Path(d) / "nope.db", Path(d) / "target.db")

    def test_database_file_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "memory.db"
            store = MemoryStore(path)
            store.close()
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
