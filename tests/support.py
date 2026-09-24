import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="releasekit-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "ReleaseKit Test")
        self.git("config", "user.email", "releasekit@example.test")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("remote", "add", "origin", "https://github.com/example/project.git")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, stderr=subprocess.PIPE).strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def config(self, specs=None, **extra):
        self.write(".releasekit.json", json.dumps({
            "version_files": specs or [{"type": "text", "path": "VERSION"}], **extra,
        }))

    def output(self):
        return Path(tempfile.mkdtemp(dir=self.temporary.name))

    def initial(self, version="1.2.3", tag=True):
        self.config()
        self.write("VERSION", version + "\n")
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n### Added\n\n")
        self.commit("initial")
        if tag:
            self.git("tag", "v" + version)

    def change(self, message="feat: add export", notes="- Export documents ([#42])."):
        self.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n### Added\n\n" + notes +
                   "\n\n## [1.2.3] - 2026-01-01\n\n### Fixed\n\n- Previous fix.\n\n[#42]: https://github.com/example/project/issues/42\n")
        self.commit(message)
