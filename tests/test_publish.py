import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from releasekit.core import inspect_release, prepare
from releasekit.github import APIError
from releasekit.publish import publish
from tests.support import RepositoryTest


class GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        state = self.server.state
        if self.headers.get("Authorization") != "Bearer test-token":
            return self.reply(401, {"message": "Unauthorized"})
        url = urlsplit(self.path)
        path = unquote(url.path)
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        method = self.command
        if method != "GET":
            state["writes"].append((method, path))
        release = state["release"]
        if method == "GET" and "/git/ref/tags/" in path:
            if not state["tag"]:
                return self.reply(404, {"message": "Not found"})
            return self.reply(200, {"object": {"type": "commit", "sha": state["tag"]}})
        if method == "POST" and path.endswith("/git/refs"):
            state["tag"] = json.loads(data)["sha"]
            return self.reply(201, {})
        if method == "GET" and "/releases/tags/" in path:
            if not release or release["draft"]:
                return self.reply(404, {"message": "Not found"})
            return self.reply(200, release)
        if method == "GET" and path.endswith("/releases"):
            return self.reply(200, [release] if release else [])
        if method == "POST" and path.endswith("/releases"):
            state["release"] = {**json.loads(data), "id": 1,
                                "upload_url": f"http://127.0.0.1:{self.server.server_port}/upload/1{{?name,label}}",
                                "html_url": "https://github.com/example/project/releases/tag/v1.3.0"}
            return self.reply(201, state["release"])
        if method == "GET" and path.endswith("/releases/1/assets"):
            return self.reply(200, list(state["assets"].values()))
        if method == "POST" and path == "/upload/1":
            name = parse_qs(url.query)["name"][0]
            if state["fail"] == name:
                state["fail"] = None
                return self.reply(500, {"message": "Interrupted upload"})
            state["next_id"] += 1
            asset = {"id": state["next_id"], "name": name, "size": len(data), "state": "uploaded",
                     "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
            state["assets"][name] = asset
            state["uploads"].append(name)
            return self.reply(201, asset)
        if method == "DELETE" and "/releases/assets/" in path:
            asset_id = int(path.rsplit("/", 1)[1])
            name = next(n for n, a in state["assets"].items() if a["id"] == asset_id)
            del state["assets"][name]
            return self.reply(200, {})
        if method == "PATCH" and path.endswith("/releases/1"):
            state["release"].update(json.loads(data))
            return self.reply(200, state["release"])
        return self.reply(404, {"message": "Unexpected endpoint: " + path})

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request


class PublishTests(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.initial()
        self.change()
        prepare(self.root, ".releasekit.json", "example/project", self.output())
        self.sha = self.commit("chore(release): v1.3.0")
        self.write("dist/app.zip", "binary payload")
        self.write("dist/data.zip", "data payload")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), GitHubHandler)
        self.server.state = {"tag": None, "release": None, "assets": {}, "writes": [], "uploads": [], "next_id": 0, "fail": None}
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.api = f"http://127.0.0.1:{self.server.server_port}"

    def publish(self, patterns="dist/*", **kwargs):
        return publish(self.root, ".releasekit.json", "example/project", self.output(), patterns,
                       "test-token", self.api, **kwargs)

    def test_partial_upload_resumes_draft_then_identical_published_retry_is_read_only(self):
        state = self.server.state
        state["fail"] = "data.zip"
        with self.assertRaisesRegex(APIError, "Interrupted upload"):
            self.publish()
        self.assertTrue(state["release"]["draft"])
        self.assertEqual(state["uploads"], ["app.zip"])
        result = self.publish()
        self.assertEqual(result["published"], "true")
        self.assertEqual(state["tag"], self.sha)
        self.assertEqual(state["uploads"].count("app.zip"), 1)
        self.assertEqual(set(state["assets"]), {"app.zip", "data.zip", "SHA256SUMS"})
        writes = len(state["writes"])
        self.publish()
        self.assertEqual(len(state["writes"]), writes)

    def test_existing_tag_for_another_commit_is_never_moved(self):
        self.server.state["tag"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "does not point"):
            self.publish(destination="EXAMPLE/PROJECT")
        self.assertEqual(self.server.state["writes"], [])

    def test_published_files_cannot_be_replaced_by_rebuilding_same_version(self):
        self.publish()
        state = self.server.state
        writes = len(state["writes"])
        self.write("dist/app.zip", "different build")
        with self.assertRaisesRegex(ValueError, "Published release artifacts differ"):
            self.publish()
        self.assertEqual(len(state["writes"]), writes)

    def test_dry_run_stages_release_files_and_verifies_checksums_on_publish(self):
        staged = self.publish(dry_run=True)
        self.assertEqual(self.server.state["writes"], [])
        folder = Path(staged["artifact-path"])
        self.assertEqual((folder / "app.zip").read_text(), "binary payload")
        self.assertEqual({path.name for path in folder.iterdir()}, {"app.zip", "data.zip", "SHA256SUMS"})
        checksum_names = {line.split("  ", 1)[1] for line in (folder / "SHA256SUMS").read_text().splitlines()}
        self.assertEqual(checksum_names, {"app.zip", "data.zip"})
        self.publish(str(folder / "*"))
        (folder / "app.zip").write_text("corrupted download")
        with self.assertRaisesRegex(ValueError, "recorded checksums"):
            self.publish(str(folder / "*"))

    def test_required_pattern_missing_is_detected_before_creating_any_release(self):
        with self.assertRaisesRegex(ValueError, "No release files match"):
            self.publish("dist/*.zip\ndist/*.apk")
        self.assertEqual(self.server.state["writes"], [])

    def test_separate_distribution_repository_publishes_changelog_without_foreign_git_tag(self):
        self.publish(destination="example/downloads")
        self.assertIsNone(self.server.state["tag"])
        expected = Path(inspect_release(self.root, ".releasekit.json", self.output())["body-path"]).read_text()
        self.assertEqual(self.server.state["release"]["body"], expected)
        self.assertNotIn("target_commitish", self.server.state["release"])
        self.server.state["release"]["body"] = "Different release notes\n"
        with self.assertRaisesRegex(ValueError, "different release notes"):
            self.publish(destination="example/downloads")
