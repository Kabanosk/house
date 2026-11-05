import unittest

from houselib.fm import parse_frontmatter, parse_scalar


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


class ParseScalarTest(unittest.TestCase):
    def test_booleans_and_quotes(self):
        self.assertIs(parse_scalar("true"), True)
        self.assertIs(parse_scalar("false"), False)
        self.assertEqual(parse_scalar('"quoted: text"'), "quoted: text")
        self.assertEqual(parse_scalar("[]"), [])


if __name__ == "__main__":
    unittest.main()
