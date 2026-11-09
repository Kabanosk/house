"""Video tooling: video-director vendored (MIT), remotion-best-practices fetched by `house setup` (not vendored)."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import HOUSE_ROOT, cli, setup

SKILL = "remotion-best-practices"


def fake_clone(calls):
    def clone(url, dest):
        calls.append((url, dest))
        (dest / "skills" / SKILL / "rules").mkdir(parents=True, exist_ok=True)
        (dest / "skills" / SKILL / "SKILL.md").write_text("---\nname: remotion-best-practices\n---\nv2\n")
        (dest / "skills" / SKILL / "rules/fonts.md").write_text("fonts\n")
    return clone


def offline(url, dest):
    raise RuntimeError("could not resolve host github.com")


class VideoDirectorVendoredTest(unittest.TestCase):
    def test_vendored_unchanged_with_license_and_attribution(self):
        d = HOUSE_ROOT / "vendor/skills/video-director"
        for f in ("SKILL.md", "LICENSE", "ATTRIBUTION.md", "scripts/qa_video.py", "scripts/check_timeline.py",
                  "references/craft.md", "references/quality.md"):
            self.assertTrue((d / f).is_file(), f)
        attribution = (d / "ATTRIBUTION.md").read_text()
        self.assertIn("github.com/win4r/video-director", attribution)
        self.assertIn("4747e5c", attribution)
        self.assertIn("MIT", (d / "LICENSE").read_text())

    def test_remotion_skill_is_not_vendored(self):
        # no LICENSE in remotion-dev/skills: fetched per machine by `house setup`, never redistributed
        self.assertFalse((HOUSE_ROOT / "vendor/skills" / SKILL).exists())


class FetchRemotionSkillTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.installed = self.home / ".claude/skills" / SKILL

    def tearDown(self):
        self._tmp.cleanup()

    def test_clones_into_cache_and_installs(self):
        calls = []
        self.assertEqual(setup.fetch_remotion_skill(self.home, clone=fake_clone(calls)), SKILL)
        self.assertEqual(calls, [(setup.REMOTION_REPO, self.home / ".cache/house/remotion-skills")])
        self.assertIn("v2", (self.installed / "SKILL.md").read_text())
        self.assertTrue((self.installed / "rules/fonts.md").is_file())

    def test_offline_without_cache_skips_quietly(self):
        self.assertIsNone(setup.fetch_remotion_skill(self.home, clone=offline))
        self.assertFalse(self.installed.exists())

    def test_offline_with_cache_installs_cached_copy(self):
        setup.fetch_remotion_skill(self.home, clone=fake_clone([]))
        (self.installed / "SKILL.md").unlink()
        self.assertEqual(setup.fetch_remotion_skill(self.home, clone=offline), SKILL)
        self.assertTrue((self.installed / "SKILL.md").is_file())

    def test_local_changes_are_backed_up(self):
        self.installed.mkdir(parents=True)
        (self.installed / "SKILL.md").write_text("my edit\n")
        setup.fetch_remotion_skill(self.home, clone=fake_clone([]))
        self.assertEqual(len(list(self.installed.parent.glob(f"{SKILL}.bak-*"))), 1)
        setup.fetch_remotion_skill(self.home, clone=fake_clone([]))  # identical now: no second backup
        self.assertEqual(len(list(self.installed.parent.glob(f"{SKILL}.bak-*"))), 1)


class SetupOutputTest(unittest.TestCase):
    def run_setup(self, fetched):
        out = io.StringIO()
        with mock.patch.object(setup, "install_user_skills", return_value=[]), \
                mock.patch.object(setup, "fetch_remotion_skill", return_value=fetched), \
                mock.patch.object(cli.doctor, "detect", return_value={}), \
                mock.patch.object(cli.doctor, "summary", return_value=""), redirect_stdout(out):
            self.assertEqual(cli.cmd_setup(None), 0)
        return out.getvalue()

    def test_prints_remotion_license_note_when_installed(self):
        text = self.run_setup(SKILL)
        self.assertIn(SKILL, text)
        self.assertIn("3 employees", text)

    def test_reports_skip_when_offline(self):
        self.assertIn("skipped", self.run_setup(None))

    def test_hint_prewarms_remotion_browser(self):
        self.assertIn("npx remotion browser ensure", "\n".join(setup.hints({})))


class DemoVideoRulesTest(unittest.TestCase):
    def test_protocol_has_house_overrides_and_production_rules(self):
        text = (HOUSE_ROOT / "core/protocols/demo-video.md").read_text()
        for needle in ("House overrides", "Studio", "TTS", "DECISION", "readable with the sound off",
                       "120–160 px", "48 px", "28 px", "0.3 s per word", "1.6–2.5×", "60–80%",
                       "FACTS.md", "Example", "x11grab", "-14 LUFS", "9:16", "qa_video.py",
                       "check_timeline.py", "glow"):
            self.assertIn(needle, text, needle)

    def test_pitch_agent_points_at_video_skills(self):
        body = (HOUSE_ROOT / "core/agents/pitch.md").read_text()
        self.assertIn("vendor/skills/video-director/SKILL.md", body)
        self.assertIn(SKILL, body)


if __name__ == "__main__":
    unittest.main()
