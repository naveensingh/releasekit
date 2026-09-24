import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from .core import inspect_release, prepare, repository_name
from .publish import publish
from .setup import initialize
from .play import upload_play
from .signing import configure_signing


def main():
    parser = argparse.ArgumentParser(prog="releasekit")
    parser.add_argument("--root", default=".", help="Consumer repository directory")
    parser.add_argument("--config", default=".releasekit.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("play", help="Upload an AAB using the publish-play Action environment")
    commands.add_parser("android-signing", help="Set up signing using the android-signing Action environment")
    init = commands.add_parser("init", help="Generate project configuration and workflows")
    init.add_argument("--preset", choices=["auto", "android", "node", "rust", "custom"], default="auto")
    init.add_argument("--toolkit-repository", default="naveensingh/releasekit")
    init.add_argument("--toolkit-ref", default="main")
    init.add_argument("--branch", default="main")
    init.add_argument("--auth", choices=["github", "app", "token"], default="github")
    init.add_argument("--build-command", default="")
    init.add_argument("--files", default="", help="Newline-separated artifact patterns")
    init.add_argument("--play-package", default="")
    init.add_argument("--tag-prefix", default=None, help="Infer v or unprefixed tags from the current release unless specified")
    for command in ("prepare", "inspect", "publish"):
        sub = commands.add_parser(command)
        if command != "inspect":
            sub.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
        sub.add_argument("--output-dir", default="")
        if command == "prepare":
            sub.add_argument("--version", default="")
        if command == "publish":
            sub.add_argument("--files", required=True)
            sub.add_argument("--destination", default="")
            sub.add_argument("--draft", action="store_true")
            sub.add_argument("--prerelease", action="store_true")
            sub.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        if args.command == "play":
            result = upload_play()
        elif args.command == "android-signing":
            result = configure_signing()
        elif args.command == "init":
            result = initialize(root, args.preset, args.toolkit_repository, args.toolkit_ref,
                                args.branch, args.auth, args.build_command, args.files, args.play_package, args.tag_prefix)
        else:
            output = Path(args.output_dir).resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="releasekit-"))
            output.mkdir(parents=True, exist_ok=True)
            if args.command == "prepare":
                repository = repository_name(root, args.repository)
                result = prepare(root, args.config, repository, output, args.version)
            elif args.command == "inspect":
                result = inspect_release(root, args.config, output)
            else:
                repository = repository_name(root, args.repository)
                result = publish(root, args.config, repository, output, args.files,
                                 os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                                 args.draft, args.prerelease, args.destination, dry_run=args.dry_run)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                for key, value in result.items():
                    delimiter = "releasekit_" + uuid.uuid4().hex
                    stream.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"releasekit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
