import json
from pathlib import Path
import re
import subprocess

from .config import MARKER
from .versions import read_version


CHECKOUT = "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
APP_TOKEN = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"


def block(text, spaces):
    return "\n".join(" " * spaces + line for line in text.splitlines())


def authentication(mode):
    if mode == "app":
        return f"""      - name: Create bot token
        id: releasekit-token
        uses: {APP_TOKEN}
        with:
          app-id: ${{{{ vars.RELEASEKIT_APP_ID }}}}
          private-key: ${{{{ secrets.RELEASEKIT_APP_PRIVATE_KEY }}}}
""", "${{ steps.releasekit-token.outputs.token }}"
    if mode == "token":
        return "", "${{ secrets.RELEASEKIT_TOKEN }}"
    return "", "${{ github.token }}"


def render_prepare(reference, branch, auth):
    auth_steps, token = authentication(auth)
    return f"""name: Prepare release PR

on:
  push:
    branches: [{json.dumps(branch)}]
  workflow_dispatch:
    inputs:
      version:
        description: 'Optional exact version (for example 2.0.0-rc.1)'
        type: string

permissions:
  contents: write
  pull-requests: write

concurrency:
  group: releasekit-prepare-${{{{ github.repository }}}}
  cancel-in-progress: true

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          ref: {json.dumps(branch)}
          fetch-depth: 0
{auth_steps}      - uses: {reference.replace('/actions/', '/actions/prepare@', 1)}
        with:
          token: {token}
          base: {json.dumps(branch)}
          version: ${{{{ inputs.version }}}}
"""


def build_setup(preset, reference):
    if preset == "node":
        return """      - uses: actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e
        with:
          node-version: '22'
"""
    if preset == "rust":
        return """      - name: Select Rust toolchain
        run: rustup default stable
"""
    if preset == "android":
        return f"""      - uses: actions/setup-java@0f481fcb613427c0f801b606911222b5b6f3083a
        with:
          distribution: temurin
          java-version: '17'
      - name: Set up signing
        id: signing
        uses: {reference.replace('/actions/', '/actions/android-signing@', 1)}
        with:
          keystore-base64: ${{{{ secrets.ANDROID_KEYSTORE_BASE64 }}}}
          key-alias: ${{{{ secrets.SIGNING_KEY_ALIAS }}}}
          key-password: ${{{{ secrets.SIGNING_KEY_PASSWORD }}}}
          store-password: ${{{{ secrets.SIGNING_STORE_PASSWORD }}}}
"""
    return ""


def render_release(reference, branch, auth, preset, command, files, play_package):
    auth_steps, token = authentication(auth)
    action = lambda name: reference.replace("/actions/", f"/actions/{name}@", 1)
    cleanup = """      - name: Remove signing key
        if: always()
        env:
          RELEASEKIT_KEYSTORE: ${{ steps.signing.outputs.keystore }}
        run: 'if [[ -n "$RELEASEKIT_KEYSTORE" ]]; then rm -f -- "$RELEASEKIT_KEYSTORE"; fi'
""" if preset == "android" else ""
    workflow = f"""name: Release

on:
  push:
    branches: [{json.dumps(branch)}]
    paths: [{json.dumps(MARKER)}]
  workflow_dispatch:
    inputs:
      release_sha:
        description: 'Exact 40-character release commit to rebuild'
        required: true
        type: string

permissions:
  contents: read

concurrency:
  group: releasekit-release-${{{{ github.repository }}}}
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      source-sha: ${{{{ steps.release.outputs.source-sha }}}}
    steps:
      - name: Validate recovery commit
        if: github.event_name == 'workflow_dispatch'
        env:
          RELEASE_SHA: ${{{{ inputs.release_sha }}}}
        run: '[[ "$RELEASE_SHA" =~ ^[0-9a-f]{{40}}$ ]]'
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ inputs.release_sha || github.sha }}}}
          persist-credentials: false
      - uses: {action('inspect')}
        id: release
{build_setup(preset, reference)}      - name: Build release files
        env:
          RELEASEKIT_VERSION: ${{{{ steps.release.outputs.version }}}}
          RELEASEKIT_TAG: ${{{{ steps.release.outputs.tag }}}}
        run: |
{block(command, 10)}
      - name: Prepare release artifacts
        id: artifacts
        uses: {action('publish')}
        with:
          dry-run: 'true'
          files: |
{block(files, 12)}
      - uses: {UPLOAD}
        with:
          name: releasekit-artifacts
          path: ${{{{ steps.artifacts.outputs.artifact-path }}}}
          if-no-files-found: error
{cleanup}
  github:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ needs.build.outputs.source-sha }}}}
          persist-credentials: false
      - uses: {DOWNLOAD}
        with:
          name: releasekit-artifacts
          path: release-artifacts
{auth_steps}      - uses: {action('publish')}
        with:
          token: {token}
          files: release-artifacts/*
"""
    if play_package:
        workflow += f"""
  google-play:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          ref: ${{{{ needs.build.outputs.source-sha }}}}
          persist-credentials: false
      - uses: {DOWNLOAD}
        with:
          name: releasekit-artifacts
          path: release-artifacts
      - uses: {action('publish-play')}
        with:
          aab: release-artifacts/*.aab
          package-name: {json.dumps(play_package)}
          service-account-json: ${{{{ secrets.PLAY_SERVICE_ACCOUNT_JSON }}}}
          track: ${{{{ vars.PLAY_TRACK || 'internal' }}}}
          metadata-path: fastlane/metadata/android
"""
    return workflow


def initialize(root, preset, toolkit_repository, toolkit_ref, branch, auth,
               build_command="", files="", play_package="", tag_prefix=None):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", toolkit_repository):
        raise ValueError("Toolkit repository must be OWNER/REPO")
    if not re.fullmatch(r"[\w./-]+", toolkit_ref):
        raise ValueError("Toolkit ref must be a branch, tag, or commit SHA")
    if preset == "auto":
        preset = next((kind for kind, file in (("android", "gradle.properties"), ("node", "package.json"),
                                             ("rust", "Cargo.toml")) if (root / file).exists()), "custom")
    versions = {
        "android": [{"type": "properties", "path": "gradle.properties", "key": "VERSION_NAME", "increment": "VERSION_CODE"}],
        "node": [{"type": "npm", "path": "package.json"}],
        "rust": [{"type": "cargo", "path": "Cargo.toml"}],
        "custom": [{"type": "text", "path": "VERSION"}],
    }[preset]
    if tag_prefix is None:
        tag_prefix = "v"
        if (root / ".git").exists() and (root / versions[0]["path"]).exists():
            current = read_version(root, versions[0])
            tags = subprocess.check_output(["git", "-C", str(root), "tag", "--list"], text=True).splitlines()
            if current in tags and "v" + current not in tags:
                tag_prefix = ""
    config = {"version_files": versions, "changelog": "CHANGELOG.md", "tag_prefix": tag_prefix, "release_branch": "release/next"}
    defaults = {
        "android": ("./gradlew assembleRelease" + (" bundleRelease" if play_package else ""),
                    "app/build/outputs/apk/**/*.apk" + ("\napp/build/outputs/bundle/**/*.aab" if play_package else "")),
        "node": ("npm ci\nnpm test --if-present\nnpm run build --if-present\nmkdir -p dist\nnpm pack --pack-destination dist", "dist/*.tgz"),
        "rust": ("cargo build --release --locked\nmkdir -p dist\ntar -C target/release -czf dist/app-linux-x86_64.tar.gz APP_BINARY", "dist/*.tar.gz"),
        "custom": ("", ""),
    }
    if preset == "rust" and (root / "Cargo.toml").exists():
        import tomllib
        manifest = tomllib.loads((root / "Cargo.toml").read_text())
        package = manifest.get("package", {})
        binaries = manifest.get("bin", [])
        if len(binaries) == 1:
            binary = binaries[0]["name"]
        elif not binaries and (root / "src/main.rs").exists():
            binary = package.get("name", "")
        else:
            binary = ""
        if binary:
            import shlex
            defaults["rust"] = (defaults["rust"][0].replace("APP_BINARY", shlex.quote(binary)), "dist/*.tar.gz")
        elif not build_command:
            raise ValueError("For a Rust workspace, library, or multiple binaries, provide --build-command and configure version_files")
    command = build_command or defaults[preset][0]
    files = files or defaults[preset][1]
    if not command or not files:
        raise ValueError("The custom preset needs --build-command and --files")
    if play_package and preset != "android":
        raise ValueError("--play-package is for the Android preset")
    if preset == "android":
        config["android_changelogs"] = "fastlane/metadata/android/en-US/changelogs"
    reference = f"{toolkit_repository}/actions/{toolkit_ref}"
    planned = {
        ".releasekit.json": json.dumps(config, indent=2) + "\n",
        ".github/workflows/releasekit-prepare.yml": render_prepare(reference, branch, auth),
        ".github/workflows/releasekit-release.yml": render_release(reference, branch, auth, preset, command, files, play_package),
    }
    if preset == "custom" and not (root / "VERSION").exists():
        planned["VERSION"] = "0.1.0\n"
    if not (root / "CHANGELOG.md").exists():
        planned["CHANGELOG.md"] = "# Changelog\n\n## [Unreleased]\n\n"
    conflicts = [name for name in planned if (root / name).exists()]
    if conflicts:
        raise ValueError("Setup would overwrite existing files: " + ", ".join(conflicts))
    for name, content in planned.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return {"preset": preset, "created": list(planned), "toolkit": toolkit_repository, "ref": toolkit_ref}
