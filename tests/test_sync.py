import tempfile
import unittest
from pathlib import Path

from houselib import registry, scaffold
from tests.helpers import EMAIL, FAKE_TOOLS, git, make_root


class SyncTest(unittest.TestCase):
    def test_clones_missing_contests_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d))
            src = scaffold.new_contest("src", None, root=root, detect=lambda: FAKE_TOOLS)
            bare = Path(d) / "x.git"
            git(Path(d), "clone", "-q", "--bare", str(src), str(bare))
            registry.add(root / "contests.yaml", "x", str(bare))
            self.assertEqual(registry.sync(root), ["x"])
            self.assertTrue((root / "contests/x/.house/spec.yaml").is_file())
            self.assertEqual(git(root / "contests/x", "config", "user.email").strip(), EMAIL)
            self.assertEqual(registry.sync(root), [])


if __name__ == "__main__":
    unittest.main()
