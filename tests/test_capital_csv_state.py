import json
from pathlib import Path
import tempfile
import unittest

from capital_csv_state import pack, restore


class CapitalCSVStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.state, self.output = [self.root / name for name in ("source", "state", "output")]
        self.source.mkdir()
        self.relative = "gyeonggi/rent/2026/2026-01-01_2026-09-09.csv.gz"
        path = self.source / self.relative
        path.parent.mkdir(parents=True)
        path.write_bytes(bytes(range(256)) * 2)
        (self.source / "manifest.json").write_text('{"status":"failed"}\n')

    def packed(self):
        pack(self.source, self.state, threshold=95, chunk_size=40)
        return json.loads((self.state / "state_files.json").read_text())

    def test_large_and_small_roundtrip_preserves_source_and_quarantine(self):
        quarantine = self.source / "quarantine/seoul/rent/2026/failed.csv.gz"
        quarantine.parent.mkdir(parents=True)
        quarantine.write_bytes(b"unvalidated source bytes")
        (self.source / ".collection.lock").write_text("lock")
        (self.source / "manifest.json.tmp").write_text("incomplete write")
        before = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        index = self.packed()
        self.assertFalse((self.state / "collection" / self.relative).exists())
        self.assertEqual((self.state / "collection/manifest.json").read_bytes(), before["manifest.json"])
        self.assertTrue(all(p.stat().st_size <= 40 for p in (self.state / "objects").iterdir()))
        restore(self.state, self.output)
        for relative in index["files"]:
            self.assertEqual((self.output / relative).read_bytes(), before[relative])
        self.assertFalse((self.output / ".collection.lock").exists())
        self.assertFalse((self.output / "manifest.json.tmp").exists())
        self.assertEqual(before, {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_corrupted_part_cannot_replace_existing_restored_file(self):
        index = self.packed()
        chunk = self.state / index["files"][self.relative]["chunks"][0]
        chunk.write_bytes(b"corrupt")
        target = self.output / self.relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"previous verified file")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            restore(self.state, self.output)
        self.assertEqual(target.read_bytes(), b"previous verified file")

    def test_index_path_escape_is_rejected(self):
        index = self.packed()
        entry = index["files"].pop(self.relative)
        index["files"]["../escaped.json"] = entry
        (self.state / "state_files.json").write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, "Unsafe checkpoint path"):
            restore(self.state, self.output)
        self.assertFalse((self.root / "escaped.json").exists())

    def test_source_and_restore_symlinks_are_rejected(self):
        outside = self.root / "outside.json"
        outside.write_text("outside")
        link = self.source / "link.json"
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.packed()
        link.unlink()
        self.packed()
        self.output.mkdir()
        (self.output / "gyeonggi").symlink_to(self.source / "gyeonggi", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            restore(self.state, self.output)
        self.assertEqual(outside.read_text(), "outside")


if __name__ == "__main__":
    unittest.main()
