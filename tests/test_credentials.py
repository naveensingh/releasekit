import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from releasekit.play import upload_play
from releasekit.signing import configure_signing


class CredentialTests(unittest.TestCase):
    def test_play_process_receives_the_selected_bundle_and_key_is_removed_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "app release.aab"
            bundle.write_bytes(b"bundle")
            captured = root / "call.json"
            executable = root / "fastlane"
            executable.write_text('#!/usr/bin/env python3\nimport json, sys\nfrom pathlib import Path\n'
                                  'args = sys.argv[1:]\nkey = Path(args[args.index("--json_key") + 1])\n'
                                  f'Path({str(captured)!r}).write_text(json.dumps({{"args":args,"key_present":key.exists()}}))\n'
                                  'sys.exit(2)\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"],
                                         "RELEASEKIT_AAB": str(bundle), "RELEASEKIT_PLAY_JSON": '{"project_id":"example"}',
                                         "RELEASEKIT_PLAY_PACKAGE": "org.example.app", "RELEASEKIT_PLAY_TRACK": "internal",
                                         "RELEASEKIT_PLAY_STATUS": "completed", "RELEASEKIT_PLAY_METADATA": "",
                                         "RELEASEKIT_PLAY_ROLLOUT": "", "RELEASEKIT_PLAY_VALIDATE": "true"}):
                with self.assertRaises(subprocess.CalledProcessError):
                    upload_play()
            call = json.loads(captured.read_text())
            self.assertTrue(call["key_present"])
            args = call["args"]
            self.assertEqual(args[args.index("--aab") + 1], str(bundle))
            self.assertEqual(args[args.index("--validate_only") + 1], "true")
            self.assertFalse(Path(args[args.index("--json_key") + 1]).exists())

    def test_signing_exports_a_private_keystore_with_the_expected_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            with patch.dict(os.environ, {"RUNNER_TEMP": directory, "GITHUB_ENV": str(environment),
                                         "RELEASEKIT_KEYSTORE_BASE64": base64.b64encode(b"test-keystore").decode(),
                                         "RELEASEKIT_KEY_ALIAS": "test", "RELEASEKIT_KEY_PASSWORD": "key-password",
                                         "RELEASEKIT_STORE_PASSWORD": "store-password"}):
                result = configure_signing()
            key = Path(result["keystore"])
            self.assertEqual(key.read_bytes(), b"test-keystore")
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertIn(str(key), environment.read_text())
