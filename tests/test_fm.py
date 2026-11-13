import unittest

from houselib.fm import parse_frontmatter, parse_scalar, set_fields


class ParseFrontmatterTest(unittest.TestCase):
    def test_parse_frontmatter_scalars_and_lists(self):
        meta, body = parse_frontmatter(
            "---\nid: coder\nmax_turns: 40\nfallback: null\ntools: [read, edit]\n---\nBody\n"
        )
        self.assertEqual(
            meta, {"id": "coder", "max_turns": 40, "fallback": None, "tools": ["read", "edit"]}
        )
        self.assertEqual(body, "Body\n")

    def test_parse_frontmatter_missing_block_raises(self):
        with self.assertRaises(ValueError):
            parse_frontmatter("no frontmatter here\n")

    def test_value_split_on_first_colon_only(self):
        meta, _ = parse_frontmatter("---\ndescription: Use when: things break\n---\n")
        self.assertEqual(meta["description"], "Use when: things break")


TASK = """---
id: T001
status: todo
owner: null
acceptance:
  - id: AC-1
    run: "pytest -q tests/test_a.py"
    timeout: 60
  - id: AC-2
    manual: golden path works
protected:
  - tests/test_a.py
depends_on: []
---
## Objective
Body: stays.
"""


class BlockListTest(unittest.TestCase):
    def test_block_list_of_mappings(self):
        meta, body = parse_frontmatter(TASK)
        self.assertEqual(meta["acceptance"], [
            {"id": "AC-1", "run": "pytest -q tests/test_a.py", "timeout": 60},
            {"id": "AC-2", "manual": "golden path works"},
        ])
        self.assertEqual(body, "## Objective\nBody: stays.\n")

    def test_block_list_of_strings(self):
        meta, _ = parse_frontmatter(TASK)
        self.assertEqual(meta["protected"], ["tests/test_a.py"])
        self.assertEqual(meta["depends_on"], [])
        self.assertEqual(meta["status"], "todo")

    def test_inline_list_still_parsed(self):
        meta, _ = parse_frontmatter("---\ntools: [read, edit]\n---\n")
        self.assertEqual(meta["tools"], ["read", "edit"])

    def test_string_item_with_colon_text(self):
        meta, _ = parse_frontmatter("---\nacceptance:\n  - golden path: login works\n---\n")
        self.assertEqual(meta["acceptance"], ["golden path: login works"])

    def test_too_deep_raises(self):
        with self.assertRaises(ValueError):
            parse_frontmatter("---\nacceptance:\n  - id: AC-1\n    run:\n      - nested\n---\n")


class SetFieldsTest(unittest.TestCase):
    def test_set_fields_replaces_and_appends(self):
        out = set_fields(TASK, {"status": "review", "branch": "task/T001-x", "owner": "coder"})
        meta, body = parse_frontmatter(out)
        self.assertEqual((meta["status"], meta["owner"], meta["branch"]), ("review", "coder", "task/T001-x"))
        self.assertEqual(body, "## Objective\nBody: stays.\n")
        self.assertIn("branch: task/T001-x\n---\n", out)

    def test_set_fields_keeps_block_lists(self):
        out = set_fields(TASK, {"status": "merged"})
        self.assertEqual(out, TASK.replace("status: todo", "status: merged"))

    def test_set_fields_quotes_when_needed(self):
        out = set_fields(TASK, {"owner": "a: b", "branch": None})
        meta, _ = parse_frontmatter(out)
        self.assertEqual(meta["owner"], "a: b")
        self.assertIsNone(meta["branch"])


class ParseScalarTest(unittest.TestCase):
    def test_double_quoted_escapes(self):
        self.assertEqual(parse_scalar('"python3 -c \'print(\\"ok\\")\'"'), "python3 -c 'print(\"ok\")'")
        self.assertEqual(parse_scalar("'it''s'"), "it's")

    def test_booleans_and_quotes(self):
        self.assertIs(parse_scalar("true"), True)
        self.assertIs(parse_scalar("false"), False)
        self.assertEqual(parse_scalar('"quoted: text"'), "quoted: text")
        self.assertEqual(parse_scalar("[]"), [])


if __name__ == "__main__":
    unittest.main()
