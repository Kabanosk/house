"""`.house/generated.json`: house knows which files it generated and never touches the rest."""
import tempfile
import unittest
from pathlib import Path

from houselib import manifest

ALL_OURS = lambda rel: True
NONE_OURS = lambda rel: False


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.c = Path(self._tmp.name)
        (self.c / ".house").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, rel: str, text: str) -> Path:
        p = self.c / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def read(self, rel: str) -> str:
        return (self.c / rel).read_text()

    def test_fresh_write_records_hashes(self):
        res = manifest.apply(self.c, {"a/x.md": "1"}, None, ALL_OURS)
        self.assertEqual(self.read("a/x.md"), "1")
        self.assertEqual(res.written, ["a/x.md"])
        manifest.save(self.c, {"a/x.md": manifest.sha256_text("1")})
        self.assertEqual(manifest.load(self.c), {"a/x.md": manifest.sha256_text("1")})
        self.assertTrue((self.c / manifest.MANIFEST).read_text().endswith("\n"))

    def test_load_without_manifest_is_none(self):
        self.assertIsNone(manifest.load(self.c))

    def test_foreign_file_backed_up_in_legacy_mode(self):
        self.put("AGENTS.md", "theirs")
        res = manifest.apply(self.c, {"AGENTS.md": "new"}, None, NONE_OURS)
        self.assertEqual(self.read("AGENTS.md.orig"), "theirs")
        self.assertEqual(self.read("AGENTS.md"), "new")
        self.assertEqual(res.backed_up, ["AGENTS.md"])

    def test_legacy_owned_overwritten_without_backup(self):
        self.put(".claude/agents/coder.md", "old generated")
        res = manifest.apply(self.c, {".claude/agents/coder.md": "new"}, None, ALL_OURS)
        self.assertFalse((self.c / ".claude/agents/coder.md.orig").exists())
        self.assertEqual(res.backed_up, [])

    def test_identical_foreign_file_not_backed_up(self):
        self.put("AGENTS.md", "same")
        res = manifest.apply(self.c, {"AGENTS.md": "same"}, None, NONE_OURS)
        self.assertEqual(res.backed_up, [])
        self.assertFalse((self.c / "AGENTS.md.orig").exists())

    def test_hand_edited_generated_file_backed_up(self):
        rel = ".claude/agents/coder.md"
        self.put(rel, "edited")
        res = manifest.apply(self.c, {rel: "gen2"}, {rel: manifest.sha256_text("gen1")}, NONE_OURS)
        self.assertEqual(self.read(rel + ".orig"), "edited")
        self.assertEqual(self.read(rel), "gen2")
        self.assertEqual(res.backed_up, [rel])

    def test_unchanged_generated_file_not_backed_up(self):
        rel = ".claude/agents/coder.md"
        self.put(rel, "gen1")
        res = manifest.apply(self.c, {rel: "gen2"}, {rel: manifest.sha256_text("gen1")}, NONE_OURS)
        self.assertEqual(res.backed_up, [])
        self.assertEqual(self.read(rel), "gen2")

    def test_existing_orig_not_overwritten(self):
        self.put("AGENTS.md", "second")
        self.put("AGENTS.md.orig", "first")
        manifest.apply(self.c, {"AGENTS.md": "new"}, None, NONE_OURS)
        self.assertEqual(self.read("AGENTS.md.orig"), "first")

    def test_stale_unchanged_removed_and_dir_pruned(self):
        self.put(".claude/agents/gone.md", "g")
        self.put(".claude/agents/keep.md", "k")
        self.put("x/only.md", "o")
        old = {r: manifest.sha256_text(t) for r, t in
               ((".claude/agents/gone.md", "g"), (".claude/agents/keep.md", "k"), ("x/only.md", "o"))}
        res = manifest.apply(self.c, {".claude/agents/keep.md": "k"}, old, NONE_OURS)
        self.assertFalse((self.c / ".claude/agents/gone.md").exists())
        self.assertTrue((self.c / ".claude/agents").is_dir())
        self.assertFalse((self.c / "x").exists())
        self.assertEqual(res.removed, [".claude/agents/gone.md", "x/only.md"])

    def test_stale_edited_kept(self):
        self.put("x/old.md", "edited")
        res = manifest.apply(self.c, {}, {"x/old.md": manifest.sha256_text("generated")}, NONE_OURS)
        self.assertTrue((self.c / "x/old.md").exists())
        self.assertEqual(res.kept_modified, ["x/old.md"])
        self.assertEqual(res.removed, [])

    def test_stale_already_missing_is_fine(self):
        res = manifest.apply(self.c, {}, {"x/old.md": manifest.sha256_text("g")}, NONE_OURS)
        self.assertEqual(res.removed, [])

    def test_unlisted_files_untouched(self):
        self.put(".claude/agents/teammate.md", "mine")
        res = manifest.apply(self.c, {".claude/agents/coder.md": "c"}, {}, NONE_OURS)
        self.assertEqual(self.read(".claude/agents/teammate.md"), "mine")
        self.assertNotIn(".claude/agents/teammate.md", res.written + res.backed_up + res.removed + res.kept_modified)

    def test_second_hand_edit_gets_its_own_backup(self):
        rel = ".claude/agents/ideator.md"
        self.put(rel, "edit1")
        manifest.apply(self.c, {rel: "gen2"}, {rel: manifest.sha256_text("gen1")}, NONE_OURS)
        self.put(rel, "edit2")
        res = manifest.apply(self.c, {rel: "gen3"}, {rel: manifest.sha256_text("gen2")}, NONE_OURS)
        self.assertEqual(self.read(rel + ".orig"), "edit1")
        backups = [p.read_text() for p in (self.c / ".claude/agents").glob("ideator.md.orig*")]
        self.assertIn("edit2", backups)
        self.assertEqual(len(res.backups), 1)
        self.assertEqual(self.read(res.backups[0]), "edit2")

    def test_symlink_is_foreign_and_target_untouched(self):
        self.put("AGENTS.md", "team rules")
        (self.c / "CLAUDE.md").symlink_to("AGENTS.md")
        res = manifest.apply(self.c, {"AGENTS.md": "ours A", "CLAUDE.md": "ours C"}, None, ALL_OURS)
        self.assertEqual(self.read("AGENTS.md"), "ours A")
        self.assertEqual(self.read("CLAUDE.md"), "ours C")
        self.assertFalse((self.c / "CLAUDE.md").is_symlink())
        self.assertTrue((self.c / "CLAUDE.md.orig").is_symlink())
        self.assertIn("CLAUDE.md", res.backed_up)

    def test_corrupt_manifest_loads_as_none(self):
        (self.c / manifest.MANIFEST).write_text("<<<<<<< HEAD\n{}\n")
        self.assertIsNone(manifest.load(self.c))
        self.assertTrue(manifest.is_corrupt(self.c))
        self.assertEqual(manifest.drift(self.c), [])

    def test_drift(self):
        self.assertEqual(manifest.drift(self.c), [])
        self.put("a.md", "changed")
        manifest.save(self.c, {"a.md": manifest.sha256_text("orig"), "b.md": manifest.sha256_text("b")})
        self.assertEqual(manifest.drift(self.c), [("a.md", "modified"), ("b.md", "missing")])


if __name__ == "__main__":
    unittest.main()
