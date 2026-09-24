import glob
import json
import os
from pathlib import Path
import subprocess
import tempfile


def upload_play():
    bundles = [p for p in glob.glob(os.environ["RELEASEKIT_AAB"], recursive=True) if Path(p).is_file()]
    if len(bundles) != 1:
        raise ValueError("The Play upload must select exactly one AAB")
    credentials = os.environ["RELEASEKIT_PLAY_JSON"]
    json.loads(credentials)
    with tempfile.TemporaryDirectory(prefix="releasekit-play-") as temporary:
        key = Path(temporary) / "service-account.json"
        key.write_text(credentials)
        key.chmod(0o600)
        args = ["fastlane", "supply", "--aab", bundles[0], "--json_key", str(key),
                "--package_name", os.environ["RELEASEKIT_PLAY_PACKAGE"],
                "--track", os.environ.get("RELEASEKIT_PLAY_TRACK", "internal"),
                "--release_status", os.environ.get("RELEASEKIT_PLAY_STATUS", "completed"),
                "--skip_upload_apk", "true", "--skip_upload_images", "true", "--skip_upload_screenshots", "true"]
        metadata = os.environ.get("RELEASEKIT_PLAY_METADATA", "")
        if metadata:
            if not Path(metadata).is_dir():
                raise ValueError(f"Play metadata directory does not exist: {metadata}")
            args.extend(["--metadata_path", metadata, "--skip_upload_metadata", "false", "--skip_upload_changelogs", "false"])
        else:
            args.extend(["--skip_upload_metadata", "true", "--skip_upload_changelogs", "true"])
        rollout = os.environ.get("RELEASEKIT_PLAY_ROLLOUT", "")
        if rollout:
            args.extend(["--rollout", rollout])
        args.extend(["--validate_only", os.environ.get("RELEASEKIT_PLAY_VALIDATE", "false")])
        subprocess.run(args, check=True)
    return {"uploaded": os.environ.get("RELEASEKIT_PLAY_VALIDATE", "false") != "true"}
