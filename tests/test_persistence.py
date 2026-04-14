import json
import tempfile
import unittest
from pathlib import Path

from app.persistence import load_json_file_with_backup, write_json_file_atomic


class PersistenceTests(unittest.TestCase):
    def test_write_json_file_atomic_writes_primary_and_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "store.json"

            write_json_file_atomic(path, [{"name": "movie mode"}])

            self.assertEqual(json.loads(path.read_text("utf-8")), [{"name": "movie mode"}])
            self.assertEqual(
                json.loads((Path(temp_dir) / "store.json.bak").read_text("utf-8")),
                [{"name": "movie mode"}],
            )

    def test_load_json_file_with_backup_recovers_from_corrupt_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "store.json"
            backup_path = Path(temp_dir) / "store.json.bak"
            path.write_text("{not valid json", encoding="utf-8")
            backup_path.write_text(json.dumps([{"name": "movie mode"}]), encoding="utf-8")

            payload = load_json_file_with_backup(path, [], label="Test store")

            self.assertEqual(payload, [{"name": "movie mode"}])
            self.assertEqual(json.loads(path.read_text("utf-8")), [{"name": "movie mode"}])
