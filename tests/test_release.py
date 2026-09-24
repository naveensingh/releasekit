import json
from pathlib import Path
import tomllib

from releasekit.config import MARKER
from releasekit.core import inspect_release, prepare
from releasekit.versions import version_order
from tests.support import RepositoryTest


class ReleaseTests(RepositoryTest):
    def test_release_pr_refresh_and_merge_keep_version_notes_and_source_together(self):
        self.initial()
        self.change()
        base = self.git("rev-parse", "HEAD")
        first = prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertEqual((first["version"], first["tag"]), ("1.3.0", "v1.3.0"))
        self.assertEqual((self.root / MARKER).read_text(), "1.3.0\n")
        self.assertIn("[#42]: https://github.com/example/project/issues/42", Path(first["body-path"]).read_text())
        old_section = "## [1.2.3] - 2026-01-01\n\n### Fixed\n\n- Previous fix."
        self.assertIn(old_section, (self.root / "CHANGELOG.md").read_text())
        self.git("reset", "--hard", base)
        self.change("fix: improve export", "- Export documents.\n- Preserve formatting.")
        refreshed = prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertEqual(refreshed["version"], "1.3.0")
        self.assertIn("Preserve formatting", Path(refreshed["body-path"]).read_text())
        sha = self.commit("chore(release): v1.3.0")
        inspected = inspect_release(self.root, ".releasekit.json", self.output())
        self.assertEqual(inspected["source-sha"], sha)
        self.assertEqual(inspected["version"], "1.3.0")
        self.assertEqual(prepare(self.root, ".releasekit.json", "example/project", self.output())["changed"], "false")

    def test_empty_changelog_does_not_change_versions(self):
        self.initial()
        result = prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertEqual(result["changed"], "false")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_first_release_uses_declared_version_without_existing_history(self):
        self.initial("0.1.0", tag=False)
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- First working release.\n")
        self.commit("feat: initial functionality")
        result = prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertEqual(result["version"], "0.1.0")

    def test_breaking_change_and_explicit_prerelease(self):
        for message, override, expected in [("feat!: change format", "", "2.0.0"),
                                             ("fix: beta build", "2.0.0-rc.1", "2.0.0-rc.1")]:
            with self.subTest(expected=expected):
                self.initial()
                self.change(message)
                result = prepare(self.root, ".releasekit.json", "example/project", self.output(), override)
                self.assertEqual(result["version"], expected)
                self.git("tag", "-d", "v1.2.3")
                self.git("reset", "--hard", "HEAD")
        self.assertLess(version_order("2.0.0-rc.2"), version_order("2.0.0-rc.10"))
        self.assertLess(version_order("2.0.0-rc.10"), version_order("2.0.0"))

    def test_android_counter_and_store_notes_refresh_from_base(self):
        self.config([{"type": "properties", "path": "gradle.properties", "increment": "VERSION_CODE"}],
                    tag_prefix="", android_changelogs="fastlane/metadata/android/en-US/changelogs")
        self.write("gradle.properties", "APP_ID=org.example.app\nVERSION_NAME=1.2.3\nVERSION_CODE=12\n")
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
        self.commit("initial")
        self.git("tag", "1.2.3")
        self.change("fix: correct rounding", "- Correct rounding ([#42]).")
        base = self.git("rev-parse", "HEAD")
        for _ in range(2):
            result = prepare(self.root, ".releasekit.json", "example/project", self.output())
            self.assertEqual(result["tag"], "1.2.4")
            self.assertIn("VERSION_CODE=13", (self.root / "gradle.properties").read_text())
            self.assertIn("• Correct rounding.", (self.root / "fastlane/metadata/android/en-US/changelogs/13.txt").read_text())
            self.git("reset", "--hard", base)

    def test_npm_package_and_lockfile_stay_in_sync(self):
        self.initial()
        self.config([{"type": "npm", "path": "package.json"}])
        self.write("package.json", '{"name":"app","version":"1.2.3"}\n')
        self.write("package-lock.json", json.dumps({"version": "1.2.3", "packages": {"": {"version": "1.2.3"},
            "node_modules/dependency": {"version": "9.0.0"}}}))
        self.change()
        result = prepare(self.root, ".releasekit.json", "example/project", self.output())
        locked = json.loads((self.root / "package-lock.json").read_text())
        self.assertEqual(locked["version"], result["version"])
        self.assertEqual(locked["packages"][""]["version"], result["version"])
        self.assertEqual(locked["packages"]["node_modules/dependency"]["version"], "9.0.0")

    def test_cargo_manifest_and_local_lock_entry_preserve_dependency_versions(self):
        self.initial()
        self.config([{"type": "cargo", "path": "Cargo.toml"}])
        self.write("Cargo.toml", '[package]\nname = "app"\nversion = "1.2.3" # keep this\n\n[dependencies]\nserde = "1"\n')
        self.write("Cargo.lock", 'version = 4\n\n[[package]]\nname = "app"\nversion = "1.2.3"\ndependencies = ["serde"]\n\n[[package]]\nname = "serde"\nversion = "1.0.0"\nsource = "registry+https://example.test"\n')
        self.change()
        prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertIn('version = "1.3.0" # keep this', (self.root / "Cargo.toml").read_text())
        packages = tomllib.loads((self.root / "Cargo.lock").read_text())["package"]
        self.assertEqual([p["version"] for p in packages], ["1.3.0", "1.0.0"])

    def test_custom_prepare_command_receives_version_and_includes_declared_outputs(self):
        self.initial()
        self.config(prepare_command="python3 sync.py", prepare_paths=["generated/version.txt"])
        self.write("sync.py", 'import os\nfrom pathlib import Path\nPath("generated").mkdir(exist_ok=True)\nPath("generated/version.txt").write_text(os.environ["RELEASEKIT_VERSION"])\n')
        self.change()
        result = prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.assertEqual((self.root / "generated/version.txt").read_text(), "1.3.0")
        self.assertIn("generated/version.txt", result["paths"])

    def test_publish_inspection_rejects_marker_version_mismatch(self):
        self.initial()
        self.change()
        prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.write("VERSION", "9.0.0\n")
        with self.assertRaisesRegex(ValueError, "does not match"):
            inspect_release(self.root, ".releasekit.json", self.output())
