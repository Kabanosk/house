"""The agent-visible house export of a `TempRoot` (`<tmp>/house`) holds no eval answer key (K5 wave 2, L1).

The agent container mounts `<tmp>/house` read-only. Neither its tree nor any git object of its repository may
hold the case files, a planted fact, a reference solution or a hidden-test source; the agent-facing `house`
commands must still work there. The contest (`<tmp>/house/contests/<name>`, its own repository) is the agent's
workspace and is checked by the fixture tests, not here.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT
from houselib.evals import chainfx, fixtures, sandbox, specfx
from tests.helpers import git
from tests.test_eval_sandbox import ScratchBase

MIN_LINE = 40  # shorter lines (imports, `return x`) are too common to identify reference or hidden-test code
PER_SOURCE = 25  # needles taken from each reference or hidden-test text


def _distinct_lines(text: str, common: str) -> list[str]:
    """Up to `PER_SOURCE` stripped lines of `text` of at least `MIN_LINE` characters that `common` does not hold."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= MIN_LINE and line not in common and line not in out:
            out.append(line)
    return out[:PER_SOURCE]


def _needles() -> dict[str, list[str]]:
    """What must never be in the export, by kind: case files, planted facts, reference code, hidden tests, sources."""
    cases_dir = HOUSE_ROOT / "evals/cases"
    chain_stub = chainfx.contest_files()
    chain_ref = chainfx.contest_files(reference=True)
    chain_common = "\n".join(chain_stub.values())
    spec_common = "\n".join(specfx.contest_files().values())
    reference = []
    for rel, text in chain_ref.items():
        reference += _distinct_lines(text, chain_stub.get(rel, ""))
    reference += _distinct_lines(specfx.REFERENCE_VALIDATE, spec_common)
    hidden = []
    for text in chainfx.hidden_tests().values():
        hidden += _distinct_lines(text, chain_common)
    for text in specfx.hidden_tests().values():
        hidden += _distinct_lines(text, spec_common)
    facts = [chainfx.ROOT_CAUSE_CODE, specfx.CHALLENGE, fixtures.RULES_FACT]
    facts += [f["answer"] for f in specfx.FACTS if len(f["answer"]) >= 6]
    return {
        "case file": [p.read_text(encoding="utf-8") for p in sorted(cases_dir.glob("*.json"))],
        "planted fact": facts,
        "reference code": reference,
        "hidden test": hidden,
        "generator source": ["def hidden_tests(", "REFERENCE_VALIDATE", "def chain_big(", "def spec_huge("],
    }


def _blobs(repo: Path) -> list[tuple[str, bytes]]:
    """Every object of `repo`'s object store (reachable or not), as `(object id, content)`."""
    out = subprocess.run(["git", "cat-file", "--batch-all-objects", "--batch"], cwd=repo, capture_output=True,
                         check=True).stdout
    objects, pos = [], 0
    while pos < len(out):
        header_end = out.index(b"\n", pos)
        sha, _, size = out[pos:header_end].split()
        start = header_end + 1
        objects.append((sha.decode(), out[start:start + int(size)]))
        pos = start + int(size) + 1
    return objects


class ExportBase(ScratchBase):
    """A scratch house repo that also holds what the export must leave out: the cases, the unit tests, the docs."""

    def setUp(self):
        super().setUp()
        shutil.copytree(HOUSE_ROOT / "evals/cases", self.src / "evals/cases")
        for rel in ("tests/test_eval_long_chain.py", "tests/test_eval_long_input.py", "tests/helpers.py",
                    "docs/plans/2026-11-18-k5-compaction-experiment.md", "docs/modules/evals.md", "README.md"):
            (self.src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(HOUSE_ROOT / rel, self.src / rel)
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "cases, tests and docs")
        self.head = git(self.src, "rev-parse", "HEAD").strip()

    def assert_no_answer_key(self, root: sandbox.TempRoot) -> None:
        needles = _needles()
        for kind, items in needles.items():
            self.assertTrue(items, f"no {kind} needles: the generator changed shape")
        contests = root.root / "contests"
        files = [p for p in root.root.rglob("*") if p.is_file() and not p.is_symlink()
                 and not p.is_relative_to(contests)]
        self.assertTrue(files)
        for path in files:
            data = path.read_bytes()
            for kind, items in needles.items():
                for needle in items:
                    where = path.relative_to(root.root)
                    self.assertFalse(needle.encode() in data, f"{kind} {needle[:60]!r} in {where}")
        objects = _blobs(root.root)
        self.assertTrue(objects)
        for sha, data in objects:
            for kind, items in needles.items():
                for needle in items:
                    self.assertFalse(needle.encode() in data, f"{kind} {needle[:60]!r} in git object {sha}")
        tracked = git(root.root, "ls-files").split()
        for excluded in ("evals/", "tests/", "houselib/evals/", "docs/plans/", "docs/specs/"):
            self.assertFalse([p for p in tracked if p.startswith(excluded)], excluded)
        self.assertIn("houselib/cli.py", tracked)
        self.assertIn("docs/modules/evals.md", tracked)  # what is not an answer key stays

    def house(self, root: sandbox.TempRoot, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(root.root / "bin/house"), *args], cwd=root.contest, env=root.env(),
                              capture_output=True, text=True, check=False)

    def assert_commands_work(self, root: sandbox.TempRoot) -> None:
        for args in (("--help",), ("check", root.name, "--all"), ("sources", root.name),
                     ("decisions", "--contest", root.name)):
            r = self.house(root, *args)
            self.assertEqual(r.returncode, 0, f"house {' '.join(args)}: {r.stdout}{r.stderr}")
            self.assertNotIn("Traceback", r.stderr, args)
        r = self.house(root, "eval", "status")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not part of this house", r.stderr)


class LongChainExportTest(ExportBase):
    def test_no_answer_key_and_commands_work(self):
        with self.temp_root({"name": "chain_big", "contest": "payments"}) as root:
            logs = b"".join(p.read_bytes() for p in (root.contest / "data/logs").iterdir())
            self.assertIn(chainfx.ROOT_CAUSE_CODE.encode(), logs)  # the contest still holds what the agent finds
            self.assert_no_answer_key(root)
            self.assert_commands_work(root)


class LongInputExportTest(ExportBase):
    def test_no_answer_key_and_commands_work(self):
        with self.temp_root({"name": "spec_huge", "contest": "halcyon"}) as root:
            self.assertTrue((root.tmp / fixtures.MATERIAL_DIR / specfx.MATERIAL).is_file())
            self.assert_no_answer_key(root)
            self.assert_commands_work(root)


class SmallExportTest(ExportBase):
    def test_sources_big_fact_is_only_in_the_contest(self):
        with self.temp_root({"name": "sources_big", "contest": "fx"}) as root:
            self.assertIn(fixtures.RULES_FACT, (root.contest / ".house/sources/rules.md").read_text())
            self.assert_no_answer_key(root)


if __name__ == "__main__":
    unittest.main()
