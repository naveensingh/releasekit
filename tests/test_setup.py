import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

from releasekit.config import MARKER
from releasekit.setup import initialize
from tests.support import RepositoryTest


class SetupTests(RepositoryTest):
    def test_generated_custom_project_can_prepare_inspect_and_stage_a_real_binary(self):
        toolkit = Path(__file__).resolve().parents[1]
        result = initialize(self.root, "custom", "example-org/releasekit", "main", "main", "github",
                            "mkdir -p dist\nprintf hello > dist/app", "dist/*")
        self.assertIn(".releasekit.json", result["created"])
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- First release.\n")
        self.commit("feat: working app")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
        def cli(*args):
            completed = subprocess.run([sys.executable, str(toolkit / "releasekit-cli.py"), "--root", str(self.root), *args],
                                       env=env, text=True, capture_output=True, check=True)
            return json.loads(completed.stdout)
        self.assertEqual(cli("prepare")["version"], "0.1.0")
        self.commit("chore(release): v0.1.0")
        workflow = yaml.safe_load((self.root / ".github/workflows/releasekit-release.yml").read_text())
        build = next(s for s in workflow["jobs"]["build"]["steps"] if s.get("name") == "Build release files")
        subprocess.run(build["run"], shell=True, cwd=self.root, check=True)
        staged = cli("publish", "--files", "dist/*", "--dry-run")
        self.assertEqual((Path(staged["artifact-path"]) / "app").read_text(), "hello")

    def test_authentication_and_separate_publish_jobs_are_generated_for_android(self):
        initialize(self.root, "android", "example-org/releasekit", "feature/actions-test", "trunk", "app",
                   "./gradlew assembleOssRelease bundlePlayRelease", "app/**/*.apk\napp/**/*.aab", "org.example.app")
        config = json.loads((self.root / ".releasekit.json").read_text())
        self.assertEqual(config["version_files"][0]["increment"], "VERSION_CODE")
        for path in (self.root / ".github/workflows").glob("*.yml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(workflow["on"]["push"]["branches"], ["trunk"])
            if path.name == "releasekit-release.yml":
                self.assertEqual(workflow["on"]["push"]["paths"], [MARKER])
        workflow = yaml.safe_load((self.root / ".github/workflows/releasekit-release.yml").read_text())
        self.assertEqual(workflow["jobs"]["github"]["needs"], "build")
        self.assertEqual(workflow["jobs"]["google-play"]["needs"], "build")
        github_steps = workflow["jobs"]["github"]["steps"]
        token = next(s for s in github_steps if s.get("id") == "releasekit-token")
        self.assertEqual(token["with"]["private-key"], "${{ secrets.RELEASEKIT_APP_PRIVATE_KEY }}")
        self.assertEqual(github_steps[-1]["uses"], "example-org/releasekit/actions/publish@feature/actions-test")

    def test_setup_does_not_overwrite_existing_release_configuration(self):
        self.write(".releasekit.json", '{"existing":true}\n')
        with self.assertRaisesRegex(ValueError, "overwrite"):
            initialize(self.root, "custom", "example/releasekit", "main", "main", "github", "make", "dist/*")
        self.assertEqual((self.root / ".releasekit.json").read_text(), '{"existing":true}\n')
        self.assertEqual(list((self.root / ".github").glob("**/*")), [])

    def test_npm_and_rust_presets_produce_valid_workflows_with_real_package_names(self):
        for preset in ("node", "rust"):
            root = self.root / preset
            root.mkdir()
            if preset == "rust":
                (root / "Cargo.toml").write_text('[package]\nname="example-cli"\nversion="0.1.0"\n')
                (root / "src").mkdir()
                (root / "src/main.rs").write_text('fn main() { println!("hello"); }')
            initialize(root, preset, "example/releasekit", "abc123", "main", "token")
            workflow = yaml.safe_load((root / ".github/workflows/releasekit-release.yml").read_text())
            build = next(s["run"] for s in workflow["jobs"]["build"]["steps"] if s.get("name") == "Build release files")
            self.assertIn("npm pack" if preset == "node" else "example-cli", build)
