"""`houselib.sources.add`: contest documents copied byte for byte into `.house/sources/` with an index."""
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from houselib import sources
from tests import helpers  # noqa: F401  (never the real Signal account)


class AddTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.contest = self.tmp / "contest"
        (self.contest / ".house").mkdir(parents=True)
        self.dir = self.contest / ".house" / "sources"

    def write_src(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def index(self) -> dict:
        return json.loads((self.dir / "index.json").read_text(encoding="utf-8"))

    # --- happy paths -------------------------------------------------------------------------------------------

    def test_file_copied_byte_for_byte_with_entry(self):
        data = "# Regulamin\r\nzażółć gęślą\r\n\r\n".encode("utf-8") + b"no newline at end"
        src = self.write_src("Rules.MD", data)
        entry = sources.add(self.contest, src=src)
        self.assertEqual((self.dir / "rules.md").read_bytes(), data)
        self.assertEqual(entry["name"], "rules.md")
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(entry["bytes"], len(data))
        text = data.decode("utf-8")
        self.assertEqual(entry["chars"], len(text))
        self.assertEqual(entry["lines"], text.count("\n") + 1)
        self.assertEqual(entry["origin"], "Rules.MD")
        self.assertRegex(entry["added"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        self.assertNotIn("binary", entry)
        self.assertEqual(self.index(), {"files": [entry]})

    def test_stdin_bytes_with_name(self):
        data = b"line one\nline two\n"
        entry = sources.add(self.contest, data=data, name="task.txt")
        self.assertEqual((self.dir / "task.txt").read_bytes(), data)
        self.assertEqual(entry["origin"], "stdin")
        self.assertEqual(entry["lines"], 2)
        self.assertEqual(entry["bytes"], len(data))
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_stdin_requires_name(self):
        with self.assertRaisesRegex(ValueError, "name"):
            sources.add(self.contest, data=b"x")
        self.assertFalse((self.dir / "index.json").exists())

    def test_exactly_one_of_src_and_data(self):
        src = self.write_src("a.txt", b"x")
        with self.assertRaises(ValueError):
            sources.add(self.contest)
        with self.assertRaises(ValueError):
            sources.add(self.contest, src=src, data=b"x", name="a.txt")
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_origin_is_base_name_never_a_path(self):
        src = self.write_src("api.md", b"x\n")
        entry = sources.add(self.contest, src=src)
        self.assertEqual(entry["origin"], "api.md")
        self.assertNotIn(str(self.tmp), json.dumps(self.index()))

    def test_explicit_name_overrides_derived(self):
        src = self.write_src("Weird Name.md", b"x\n")
        entry = sources.add(self.contest, src=src, name="rules.md")
        self.assertEqual(entry["name"], "rules.md")
        self.assertEqual(entry["origin"], "Weird Name.md")

    def test_src_may_be_a_string_path(self):
        src = self.write_src("a.txt", b"x\n")
        sources.add(self.contest, src=str(src))
        self.assertEqual((self.dir / "a.txt").read_bytes(), b"x\n")

    def test_index_sorted_by_name_utf8_sorted_keys_and_no_temp_files(self):
        for n in ("b.txt", "a.txt", "c.txt"):
            sources.add(self.contest, data=n.encode(), name=n)
        raw = (self.dir / "index.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual([e["name"] for e in data["files"]], ["a.txt", "b.txt", "c.txt"])
        for e in data["files"]:
            self.assertEqual(list(e), sorted(e))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         [".gitignore", "a.txt", "b.txt", "c.txt", "index.json"])

    def test_index_written_as_utf8_not_ascii_escaped(self):
        # The origin keeps non-ASCII letters; the file name is derived to ASCII.
        src = self.write_src("Zażółć.txt", b"x\n")
        sources.add(self.contest, src=src)
        raw = (self.dir / "index.json").read_bytes()
        self.assertIn("Zażółć.txt".encode("utf-8"), raw)

    # --- counting ----------------------------------------------------------------------------------------------

    def test_counts(self):
        cases = [(b"", 0), (b"a", 1), (b"a\n", 1), (b"a\nb", 2), (b"\n", 1), (b"a\n\nb\n", 3)]
        for i, (data, lines) in enumerate(cases):
            entry = sources.add(self.contest, data=data, name=f"f{i}.txt")
            self.assertEqual(entry["lines"], lines, data)
            self.assertEqual(entry["chars"], len(data))

    def test_invalid_utf8_counts_with_replacement(self):
        data = b"ok \xff\xfe bad\n"
        entry = sources.add(self.contest, data=data, name="latin.txt")
        self.assertEqual(entry["chars"], len(data.decode("utf-8", errors="replace")))
        self.assertEqual((self.dir / "latin.txt").read_bytes(), data)
        self.assertNotIn("binary", entry)

    def test_binary_flagged_and_still_counted(self):
        data = b"%PDF-1.4\n\x00\x01\x02 binary \xff\n" + b"x" * 100
        entry = sources.add(self.contest, data=data, name="doc.pdf")
        self.assertIs(entry["binary"], True)
        self.assertEqual(entry["chars"], len(data.decode("utf-8", errors="replace")))
        self.assertEqual(entry["lines"], 3)  # two newlines plus an unterminated last line
        self.assertEqual((self.dir / "doc.pdf").read_bytes(), data)

    def test_nul_after_first_8k_is_not_binary(self):
        data = b"a" * 8192 + b"\x00tail\n"
        entry = sources.add(self.contest, data=data, name="late.txt")
        self.assertNotIn("binary", entry)

    def test_nul_in_first_8k_is_binary_for_src_too(self):
        src = self.write_src("blob.bin", b"x" * 8191 + b"\x00" + b"y" * 10)
        self.assertIs(sources.add(self.contest, src=src)["binary"], True)

    def test_large_file_is_streamed_not_loaded_twice(self):
        unit = "zażółć gęślą jaźń 0123456789\n"  # multibyte chars so chunk boundaries split sequences
        text = (unit * (500_000 // len(unit) + 1))[:500_000]
        data = text.encode("utf-8")
        src = self.write_src("regulations.txt", data)
        with mock.patch.object(sources, "_CHUNK", 4097), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("loaded whole file")), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError("loaded whole file")):
            entry = sources.add(self.contest, src=src)
        self.assertEqual(entry["chars"], 500_000)
        self.assertEqual(entry["lines"], text.count("\n") + (0 if text.endswith("\n") else 1))
        self.assertEqual(entry["bytes"], len(data))
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((self.dir / "regulations.txt").read_bytes(), data)

    # --- name rule ---------------------------------------------------------------------------------------------

    def test_derived_name_rules(self):
        cases = {
            "My Rules (v2).MD": "my-rules--v2-.md",
            "Zażółć.txt": "za----.txt",
            "..hidden": "hidden",
            "_x.txt": "x.txt",
            "A_b-c.d": "a_b-c.d",
        }
        for base, want in cases.items():
            src = self.write_src(base, b"x\n")
            self.assertEqual(sources.add(self.contest, src=src)["name"], want, base)

    def test_derived_name_empty_or_too_long_is_error(self):
        for base in ("---", "_.", "a" * 82):
            src = self.write_src(base, b"x")
            with self.assertRaises(ValueError, msg=base):
                sources.add(self.contest, src=src)
        src = self.write_src("a" * 81, b"x")
        self.assertEqual(len(sources.add(self.contest, src=src)["name"]), 81)

    def test_bad_explicit_names_rejected_not_rewritten(self):
        bad = ["../x", "/etc/passwd", "a/b", "..", ".", ".hidden", "-x", "Upper.txt", "sp ace", "", "a\\b",
               "x" * 82, "ż.txt", "index.json", "a\nb", "a\x00b"]
        for name in bad:
            with self.assertRaises(ValueError, msg=repr(name)):
                sources.add(self.contest, data=b"x", name=name)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))
        self.assertFalse((self.contest / ".house" / "x").exists())
        self.assertFalse((self.tmp / "x").exists())

    def test_explicit_name_with_dots_inside_is_fine(self):
        for name in ("a..b", "v1.2.3-rc_1.txt", "0", "x" * 81):
            sources.add(self.contest, data=b"x", name=name)

    def test_index_json_is_reserved_also_as_derived_name(self):
        src = self.write_src("Index.JSON", b"{}")
        with self.assertRaisesRegex(ValueError, "index.json"):
            sources.add(self.contest, src=src)
        self.assertFalse((self.dir / "index.json").exists())

    # --- refusals ----------------------------------------------------------------------------------------------

    def test_symlink_source_refused_and_target_not_copied(self):
        target = self.write_src("secret.txt", b"secret\n")
        link = self.tmp / "link.txt"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.add(self.contest, src=link)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_dangling_symlink_refused_as_symlink(self):
        link = self.tmp / "dangling.txt"
        link.symlink_to(self.tmp / "nowhere")
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.add(self.contest, src=link)

    def test_missing_source_is_clear_error(self):
        with self.assertRaisesRegex(ValueError, "not found|does not exist"):
            sources.add(self.contest, src=self.tmp / "nope.txt")

    def test_directory_source_is_clear_error(self):
        d = self.tmp / "adir"
        d.mkdir()
        with self.assertRaisesRegex(ValueError, "directory|not a regular file"):
            sources.add(self.contest, src=d, name="adir")

    def test_readding_same_name_refused_and_nothing_changes(self):
        sources.add(self.contest, data=b"old\n", name="rules.md")
        before_index = (self.dir / "index.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "replace"):
            sources.add(self.contest, data=b"new\n", name="rules.md")
        self.assertEqual((self.dir / "rules.md").read_bytes(), b"old\n")
        self.assertEqual((self.dir / "index.json").read_bytes(), before_index)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".gitignore", "index.json", "rules.md"])

    def test_untracked_file_with_same_name_is_not_overwritten_without_replace(self):
        self.dir.mkdir()
        (self.dir / "rules.md").write_bytes(b"hand made\n")
        with self.assertRaisesRegex(ValueError, "replace"):
            sources.add(self.contest, data=b"new\n", name="rules.md")
        self.assertEqual((self.dir / "rules.md").read_bytes(), b"hand made\n")

    def test_replace_updates_file_and_index_and_refreshes_added(self):
        with mock.patch.object(sources, "_now", return_value="2026-01-01T00:00:00+00:00"):
            first = sources.add(self.contest, data=b"old\n", name="rules.md")
        sources.add(self.contest, data=b"other\n", name="other.md")
        with mock.patch.object(sources, "_now", return_value="2026-02-02T00:00:00+00:00"):
            second = sources.add(self.contest, data=b"new text\nmore\n", name="rules.md", replace=True)
        self.assertEqual((self.dir / "rules.md").read_bytes(), b"new text\nmore\n")
        self.assertEqual(first["added"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(second["added"], "2026-02-02T00:00:00+00:00")
        self.assertEqual(second["lines"], 2)
        files = self.index()["files"]
        self.assertEqual([e["name"] for e in files], ["other.md", "rules.md"])
        self.assertEqual(files[1], second)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         [".gitignore", "index.json", "other.md", "rules.md"])

    def test_replace_of_a_new_name_just_adds(self):
        entry = sources.add(self.contest, data=b"x\n", name="new.md", replace=True)
        self.assertEqual(self.index()["files"], [entry])

    def test_replace_may_drop_binary_flag(self):
        sources.add(self.contest, data=b"\x00bin", name="d.dat")
        entry = sources.add(self.contest, data=b"text\n", name="d.dat", replace=True)
        self.assertNotIn("binary", entry)
        self.assertNotIn("binary", self.index()["files"][0])

    def test_house_dir_symlink_refused(self):
        real = self.tmp / "elsewhere"
        real.mkdir()
        (self.contest / ".house").rmdir()
        (self.contest / ".house").symlink_to(real)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual(list(real.iterdir()), [])

    def test_sources_dir_symlink_refused(self):
        real = self.tmp / "elsewhere"
        real.mkdir()
        self.dir.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual(list(real.iterdir()), [])

    def test_index_entry_name_must_be_a_string(self):
        self.dir.mkdir()
        (self.dir / "index.json").write_text('{"files": [{"name": 5}]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "index.json"):
            sources.add(self.contest, data=b"x", name="a.txt")

    def test_replace_over_a_directory_is_value_error(self):
        (self.dir / "rules.md").mkdir(parents=True)
        for replace in (False, True):
            with self.assertRaisesRegex(ValueError, "directory"):
                sources.add(self.contest, data=b"x", name="rules.md", replace=replace)
        self.assertTrue((self.dir / "rules.md").is_dir())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["rules.md"])

    def test_source_opened_with_nofollow_closes_the_swap_race(self):
        src = self.write_src("a.txt", b"real\n")
        target = self.write_src("secret.txt", b"secret\n")
        real_exists = Path.is_symlink

        def swap_after_check(path):  # the check passes, then the file is swapped for a link
            result = real_exists(path)
            if path == src:
                src.unlink()
                src.symlink_to(target)
            return result

        with mock.patch.object(Path, "is_symlink", swap_after_check):
            with self.assertRaisesRegex(ValueError, "symlink"):
                sources.add(self.contest, src=src)
        self.assertFalse((self.dir / "a.txt").exists())
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name != "index.json"], [".gitignore"])

    def test_corrupt_index_is_error_and_untouched(self):
        self.dir.mkdir()
        (self.dir / "index.json").write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "index.json"):
            sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual((self.dir / "index.json").read_text(encoding="utf-8"), "{not json")
        self.assertFalse((self.dir / "a.txt").exists())

    def test_symlinked_index_is_an_error_and_the_target_is_untouched(self):
        self.dir.mkdir()
        real = self.tmp / "elsewhere.json"
        real.write_text('{"files": []}', encoding="utf-8")
        (self.dir / "index.json").symlink_to(real)
        with self.assertRaisesRegex(ValueError, "index.json.*symlink"):
            sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual(real.read_text(encoding="utf-8"), '{"files": []}')
        self.assertTrue((self.dir / "index.json").is_symlink())
        self.assertFalse((self.dir / "a.txt").exists())

    def test_index_that_is_not_a_regular_file_is_an_error_not_a_hang(self):
        self.dir.mkdir()
        os.mkfifo(self.dir / "index.json")
        with self.assertRaisesRegex(ValueError, "index.json"):
            sources.add(self.contest, data=b"x", name="a.txt")
        self.assertFalse((self.dir / "a.txt").exists())

    def test_first_add_writes_gitignore_for_temp_files_and_keeps_an_existing_one(self):
        sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual((self.dir / ".gitignore").read_text(encoding="utf-8"), ".add-*\n")
        (self.dir / ".gitignore").write_text("# mine\n.add-*\n*.tmp\n", encoding="utf-8")
        sources.add(self.contest, data=b"y", name="b.txt")
        self.assertEqual((self.dir / ".gitignore").read_text(encoding="utf-8"), "# mine\n.add-*\n*.tmp\n")

    def test_a_symlinked_gitignore_is_never_written_through(self):
        self.dir.mkdir()
        target = self.tmp / "target"
        target.write_text("keep\n", encoding="utf-8")
        (self.dir / ".gitignore").symlink_to(target)
        sources.add(self.contest, data=b"x", name="a.txt")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_the_temp_file_pattern_is_ignored_by_git(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.contest)], check=True)
        sources.add(self.contest, data=b"x", name="a.txt")
        (self.dir / ".add-half").write_bytes(b"half")
        out = subprocess.run(["git", "-C", str(self.contest), "status", "--porcelain", "-uall"], check=True,
                             capture_output=True, text=True).stdout
        self.assertIn(".house/sources/a.txt", out)
        self.assertNotIn(".add-half", out)

    # --- atomicity ---------------------------------------------------------------------------------------------

    def test_failed_copy_leaves_no_half_file(self):
        src = self.write_src("a.txt", b"x" * 100)
        with mock.patch("shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sources.add(self.contest, src=src)
        self.assertEqual([p.name for p in self.dir.iterdir()], [".gitignore"])

    def test_failed_replace_keeps_old_file_and_cleans_temp(self):
        sources.add(self.contest, data=b"old\n", name="a.txt")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                sources.add(self.contest, data=b"new\n", name="a.txt", replace=True)
        self.assertEqual((self.dir / "a.txt").read_bytes(), b"old\n")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".gitignore", "a.txt", "index.json"])

    def test_added_file_is_world_readable_not_0600(self):
        sources.add(self.contest, data=b"x\n", name="a.txt")
        mode = (self.dir / "a.txt").stat().st_mode & 0o777
        self.assertEqual(mode & 0o044, 0o044)

    def test_does_not_touch_git(self):
        sources.add(self.contest, data=b"x\n", name="a.txt")
        self.assertFalse((self.contest / ".git").exists())


class ListingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.contest = Path(self._tmp.name) / "mycontest"
        (self.contest / ".house").mkdir(parents=True)
        self.dir = self.contest / ".house" / "sources"

    def add(self, name: str, text: str | bytes) -> dict:
        data = text.encode("utf-8") if isinstance(text, str) else text
        return sources.add(self.contest, data=data, name=name)

    def section(self, out: str, name: str) -> str:
        """The block of `out` that belongs to file `name` (from its header line to the next header)."""
        blocks = out.split("\n\n")
        found = [b for b in blocks if b.startswith(name + ":")]
        self.assertEqual(len(found), 1, out)
        return found[0]

    def test_empty_directory_prints_hint(self):
        for make in (False, True):
            if make:
                self.dir.mkdir()
            out = sources.listing(self.contest)
            self.assertIn("no sources yet", out)
            self.assertIn("house sources add", out)
            self.assertEqual(list(self.dir.iterdir()) if make else [], [])

    def test_no_house_dir_is_also_empty(self):
        out = sources.listing(self.contest.parent / "elsewhere")
        self.assertIn("no sources yet", out)

    def test_sizes_tokens_lines(self):
        text = "# Rules\nzażółć\nthree\n"
        self.add("rules.md", text)
        out = sources.listing(self.contest)
        self.assertIn(f"rules.md: {len(text)} chars, ~{len(text) // 4} tokens, 3 lines", out)

    def test_headings_with_line_numbers_levels_and_cut(self):
        long = "x" * 300
        text = ("Intro\n# One\n\ntext\n## Two ##\n###### Six\n####### seven hashes\n#nospace\n"
                f"  ### Indented\n    # code indent\n# {long}\n# C#\n")
        self.add("a.md", text)
        sec = self.section(sources.listing(self.contest), "a.md")
        self.assertIn("\n  L2  # One\n", sec)
        self.assertIn("\n  L5  ## Two\n", sec)
        self.assertIn("\n  L6  ###### Six\n", sec)
        self.assertIn("\n  L9  ### Indented\n", sec)
        self.assertIn("  L12  # C#", sec)
        for bad in ("seven hashes", "nospace", "code indent"):
            self.assertNotIn(bad, sec)
        cut = [ln for ln in sec.splitlines() if ln.startswith("  L11  ")]
        self.assertEqual(len(cut), 1)
        self.assertLess(len(cut[0]), 130)
        self.assertIn("x" * 90, cut[0])
        self.assertNotIn("x" * 150, cut[0])

    def test_headings_inside_fences_are_ignored(self):
        text = ("# Real\n```\n# not a heading\n~~~\n# still code\n```\n## After\n"
                "~~~~\n# tilde code\n~~~\n# still inside (closer too short)\n~~~~~\n### After2\n"
                "````python\n```\n# inner\n````\n#### After3\n")
        self.add("f.md", text)
        sec = self.section(sources.listing(self.contest), "f.md")
        shown = [ln.strip() for ln in sec.splitlines() if ln.startswith("  L")]
        self.assertEqual(shown, ["L1  # Real", "L7  ## After", "L13  ### After2", "L18  #### After3"])

    def test_unclosed_fence_hides_the_rest_and_warns(self):
        self.add("u.md", "# A\n```\n# B\n")
        sec = self.section(sources.listing(self.contest), "u.md")
        self.assertIn("L1  # A", sec)
        self.assertNotIn("# B", sec)
        self.assertIn("warning: code fence opened at L2 is never closed; headings after it are not shown", sec)

    def test_closed_fence_has_no_warning(self):
        self.add("k.md", "# A\n```\n# B\n```\n# C\n")
        self.assertNotIn("never closed", sources.listing(self.contest))

    def test_unclosed_fence_on_unterminated_last_line(self):
        self.add("u.md", "# A\n~~~~")
        self.assertIn("opened at L2 is never closed", sources.listing(self.contest))

    def test_invalid_index_names_listed_as_invalid_and_never_read(self):
        outside = Path(self._tmp.name) / "outside.md"
        outside.write_text("# Outside secret\n")
        self.add("a.md", "# A\n")
        idx = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
        for bad in ("../../outside.md", str(outside), "Up.md", "a/b"):
            idx["files"].append({"name": bad, "sha256": "x"})
        (self.dir / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        real_open = sources._open_nofollow
        opened = []

        def spy(path):
            opened.append(Path(path))
            return real_open(path)

        with mock.patch.object(sources, "_open_nofollow", spy):
            out = sources.listing(self.contest)
        self.assertNotIn("Outside secret", out)
        self.assertEqual([p.name for p in opened], ["a.md"])
        self.assertEqual(out.count("invalid name in index"), 4)
        self.assertIn("L1  # A", out)

    def test_symlinked_index_is_reported_and_never_read(self):
        self.add("a.md", "# A\n")
        real = Path(self._tmp.name) / "other.json"
        real.write_text(json.dumps({"files": [{"name": "ghost.md", "sha256": "x"}]}), encoding="utf-8")
        (self.dir / "index.json").unlink()
        (self.dir / "index.json").symlink_to(real)
        out = sources.listing(self.contest)
        self.assertIn("warning:", out)
        self.assertIn("symlink", out)
        self.assertNotIn("ghost.md", out)
        self.assertIn("L1  # A", out)

    def test_invalid_names_on_disk_are_listed_with_repr_and_never_read(self):
        self.add("a.md", "# A\n")
        bad = ["Up.md", "two words.md", "ctl\x1b[31mred.md", "-lead.md"]
        for name in bad:
            (self.dir / name).write_text("# Secret heading\n")
        opened = []
        real_open = sources._open_nofollow

        def spy(path):
            opened.append(Path(path).name)
            return real_open(path)

        with mock.patch.object(sources, "_open_nofollow", spy):
            out = sources.listing(self.contest)
        self.assertEqual(opened, ["a.md"])
        self.assertNotIn("Secret heading", out)
        self.assertNotIn("\x1b", out)  # repr, so no raw control characters reach the terminal
        for name in bad:
            self.assertIn(f"{name!r}: invalid name (not read)", out)
        self.assertTrue(out.startswith("5 sources in .house/sources/"), out)

    def test_hint_uses_resolved_contest_name(self):
        link = Path(self._tmp.name) / "alias"
        link.symlink_to(self.contest)
        (self.dir).mkdir()
        out = sources.listing(link / ".." / "alias")
        self.assertIn("house sources add mycontest ", out)

    def test_crlf_line_length_excludes_cr(self):
        self.add("c.txt", ("a" * 2000 + "\r\n") * 3)
        self.assertNotIn("longer than", sources.listing(self.contest))
        self.add("d.txt", ("a" * 2001 + "\r\n") * 2)
        self.assertIn("2 lines longer than", sources.listing(self.contest))

    def test_docstring_says_setext_headings_not_listed(self):
        self.assertIn("Setext", sources.listing.__doc__)

    def test_backtick_fence_not_closed_by_tilde_or_info_string(self):
        self.add("g.md", "```\n~~~\n# x\n``` info\n# y\n```\n# z\n")
        sec = self.section(sources.listing(self.contest), "g.md")
        shown = [ln.strip() for ln in sec.splitlines() if ln.startswith("  L")]
        self.assertEqual(shown, ["L7  # z"])

    def test_crlf_headings(self):
        self.add("c.md", "# One\r\n## Two\r\n")
        sec = self.section(sources.listing(self.contest), "c.md")
        self.assertIn("L1  # One\n", sec + "\n")
        self.assertIn("L2  ## Two", sec)
        self.assertNotIn("\r", sec)

    def test_heading_cap_with_and_k_more(self):
        self.add("big.md", "".join(f"# H{i}\n" for i in range(1, 71)))
        sec = self.section(sources.listing(self.contest), "big.md")
        shown = [ln for ln in sec.splitlines() if ln.startswith("  L")]
        self.assertEqual(len(shown), 50)
        self.assertEqual(shown[-1], "  L50  # H50")
        self.assertIn("and 20 more", sec)

    def test_exactly_at_cap_has_no_more_line(self):
        self.add("e.md", "".join(f"# H{i}\n" for i in range(50)))
        self.assertNotIn("more", sources.listing(self.contest))

    def test_long_line_warning(self):
        self.add("w.md", "ok\n" + "a" * 2001 + "\n" + "b" * 2000 + "\n" + "c" * 3000)
        sec = self.section(sources.listing(self.contest), "w.md")
        self.assertIn("warning", sec)
        self.assertIn("2 lines longer than 2000 characters", sec)
        self.assertIn("first at L2", sec)

    def test_no_long_line_warning_at_2000(self):
        self.add("w.md", "a" * 2000 + "\n")
        self.assertNotIn("warning", sources.listing(self.contest))

    def test_clean_file_has_no_warning(self):
        self.add("ok.md", "# T\ntext\n")
        self.assertNotIn("warning", sources.listing(self.contest))
        self.assertNotIn("not in index", sources.listing(self.contest))

    def test_changed_sum_warns_that_line_ranges_may_have_moved(self):
        self.add("a.md", "# T\ntext\n")
        self.add("b.md", "# T\ntext\n")
        (self.dir / "a.md").write_bytes(b"# T\nextra line\ntext\n")  # edited behind the index
        out = sources.listing(self.contest)
        self.assertIn("line ranges in spec.yaml may have moved", self.section(out, "a.md"))
        self.assertNotIn("moved", self.section(out, "b.md"))
        self.assertIn("3 lines", self.section(out, "a.md"))  # figures describe the file as it is now

    def test_binary_listed_without_headings(self):
        self.add("doc.pdf", b"%PDF\n\x00\x01 # not a heading\n# nor this\n")
        sec = self.section(sources.listing(self.contest), "doc.pdf")
        self.assertIn("binary", sec)
        self.assertNotIn("  L", sec)
        self.assertNotIn("warning", sec)

    def test_binary_long_line_not_warned(self):
        self.add("blob.bin", b"\x00" + b"a" * 5000)
        self.assertNotIn("longer than", sources.listing(self.contest))

    def test_file_not_in_index_is_noted(self):
        self.add("a.md", "# A\n")
        (self.dir / "hand.md").write_text("# Hand\n", encoding="utf-8")
        out = sources.listing(self.contest)
        sec = self.section(out, "hand.md")
        self.assertIn("not in index", sec)
        self.assertIn("L1  # Hand", sec)
        self.assertNotIn("not in index", self.section(out, "a.md"))

    def test_index_entry_without_file_is_missing(self):
        self.add("a.md", "# A\n")
        self.add("gone.md", "# G\n")
        (self.dir / "gone.md").unlink()
        out = sources.listing(self.contest)
        self.assertIn("missing", self.section(out, "gone.md"))
        self.assertNotIn("missing", self.section(out, "a.md"))

    def test_temp_files_dotfiles_index_and_subdirs_skipped(self):
        self.add("a.md", "# A\n")
        (self.dir / ".add-xyz123").write_bytes(b"half")
        (self.dir / ".hidden").write_text("# h\n")
        (self.dir / "subdir").mkdir()
        out = sources.listing(self.contest)
        for bad in (".add-xyz123", ".hidden", "subdir", "index.json"):
            self.assertNotIn(bad, out)

    def test_symlink_inside_sources_not_followed(self):
        self.add("a.md", "# A\n")
        secret = Path(self._tmp.name) / "secret.md"
        secret.write_text("# Secret heading\n")
        (self.dir / "link.md").symlink_to(secret)
        out = sources.listing(self.contest)
        self.assertNotIn("Secret heading", out)
        self.assertIn("symlink", self.section(out, "link.md"))

    def test_symlinked_sources_dir_refused(self):
        real = Path(self._tmp.name) / "real"
        real.mkdir()
        self.dir.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.listing(self.contest)

    def test_corrupt_index_is_reported_not_fatal(self):
        self.add("a.md", "# A\n")
        (self.dir / "index.json").write_text("{nope", encoding="utf-8")
        out = sources.listing(self.contest)
        self.assertIn("index.json", out)
        self.assertIn("L1  # A", out)

    def test_files_sorted_by_name(self):
        for n in ("b.md", "a.md", "c.md"):
            self.add(n, "x\n")
        out = sources.listing(self.contest)
        self.assertLess(out.index("a.md:"), out.index("b.md:"))
        self.assertLess(out.index("b.md:"), out.index("c.md:"))

    def test_large_file_streamed_in_one_pass_and_fast(self):
        unit = "# Zażółć {i}\nbody gęślą jaźń 0123456789 0123456789\n"
        text = "".join(unit.format(i=i) for i in range(1, 12000))[:500_000]
        data = text.encode("utf-8")
        (self.dir).mkdir(parents=True)
        entry = self.add("big.md", data)
        real_read_text = Path.read_text

        def guarded(path, *args, **kwargs):  # only the small index may be read whole
            if path.name != "index.json":
                raise AssertionError("loaded whole file")
            return real_read_text(path, *args, **kwargs)

        with mock.patch.object(sources, "_CHUNK", 4099), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("loaded whole file")), \
                mock.patch.object(Path, "read_text", guarded):
            start = time.monotonic()
            out = sources.listing(self.contest)
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn(f"{len(text)} chars", out)
        self.assertNotIn("moved", out)  # sha256 recomputed in the same pass equals the index
        self.assertIn("L1  # Zażółć 1\n", out + "\n")
        self.assertIn("more", out)
        self.assertEqual(entry["bytes"], len(data))

    def test_headings_across_chunk_boundaries(self):
        text = "".join(f"## Heading number {i}\n" for i in range(40))
        self.add("c.md", text)
        with mock.patch.object(sources, "_CHUNK", 7):
            sec = self.section(sources.listing(self.contest), "c.md")
        shown = [ln for ln in sec.splitlines() if ln.startswith("  L")]
        self.assertEqual(shown[0], "  L1  ## Heading number 0")
        self.assertEqual(shown[-1], "  L40  ## Heading number 39")

    def test_listing_does_not_modify_anything(self):
        self.add("a.md", "# A\n")
        before = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        sources.listing(self.contest)
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
