import glob
import hashlib
from pathlib import Path
import shutil
import tempfile

from .core import inspect_release
from .github import GitHub
from .versions import parse_version


def collect_assets(root, patterns):
    files = {}
    for pattern in patterns.splitlines():
        pattern = pattern.strip()
        if not pattern:
            continue
        candidates = sorted(Path(p).resolve() for p in glob.glob(str(root / pattern), recursive=True) if Path(p).is_file())
        if not candidates:
            raise ValueError(f"No release files match {pattern}")
        for path in candidates:
            if path.name == "SHA256SUMS":
                continue
            if path.name in files and files[path.name] != path:
                raise ValueError(f"Two release assets have the name {path.name}; give them distinct names")
            if any(c in path.name for c in "\r\n\\"):
                raise ValueError(f"Asset name cannot be represented in SHA256SUMS: {path.name!r}")
            files[path.name] = path
    if not files:
        raise ValueError("At least one release file is required")
    return dict(sorted(files.items()))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(root, config_path, repository, output_dir, patterns, token, api_url,
            draft=False, prerelease=False, destination="", client=None, dry_run=False):
    release_info = inspect_release(root, config_path, output_dir)
    prerelease = prerelease or parse_version(release_info["version"])[1] is not None
    assets = collect_assets(root, patterns)
    hashes = {name: digest(path) for name, path in assets.items()}
    checksum_text = "".join(f"{sha}  {name}\n" for name, sha in sorted(hashes.items()))
    for pattern in patterns.splitlines():
        if pattern.strip():
            for candidate in glob.glob(str(root / pattern.strip()), recursive=True):
                previous = Path(candidate)
                if previous.is_file() and previous.name == "SHA256SUMS" and previous.read_text() != checksum_text:
                    raise ValueError("Downloaded release artifacts do not match their recorded checksums")
    checksums = output_dir / "SHA256SUMS"
    checksums.write_text(checksum_text)
    assets[checksums.name] = checksums
    hashes[checksums.name] = digest(checksums)
    bundle = Path(tempfile.mkdtemp(prefix="artifacts-", dir=output_dir))
    for name, path in assets.items():
        shutil.copy2(path, bundle / name)
    result = {**release_info, "checksums": str(checksums),
              "artifact-path": str(bundle), "published": "false"}
    if dry_run:
        return result

    destination = destination or repository
    same_repository = destination.casefold() == repository.casefold()
    github = client or GitHub(destination, token, api_url)
    tag = release_info["tag"]
    body = Path(release_info["body-path"]).read_text().rstrip() + "\n"
    release = github.get_release(tag)
    if release and (release.get("body") or "").rstrip() != body.rstrip():
        raise ValueError(f"Existing release {tag} has different release notes")
    if same_repository:
        github.ensure_tag(tag, release_info["source-sha"])

    if not release:
        payload = {"tag_name": tag, "name": tag, "body": body, "draft": True, "prerelease": prerelease}
        if same_repository:
            payload["target_commitish"] = release_info["source-sha"]
        release = github.request("POST", f"{github.repo}/releases", payload)

    existing = {asset["name"]: asset for asset in github.assets(release["id"])}
    changes = []
    for name, path in assets.items():
        previous = existing.get(name)
        same = previous and previous.get("state") == "uploaded" and previous["size"] == path.stat().st_size
        if same:
            same = github.asset_digest(previous) == hashes[name]
        if not same:
            changes.append((path, previous))
    if not release["draft"]:
        if changes:
            raise ValueError("Published release artifacts differ. Publish a new version instead of replacing them")
        return {**result, "url": release["html_url"], "published": "true"}

    for path, previous in changes:
        if previous:
            github.request("DELETE", f"{github.repo}/releases/assets/{previous['id']}")
        uploaded = github.upload(release, path)
        if uploaded.get("state") != "uploaded" or github.asset_digest(uploaded) != hashes[path.name]:
            raise ValueError(f"Could not verify uploaded asset {path.name}; the release remains a draft")
    stale = set(existing) - set(assets)
    for name in stale:
        github.request("DELETE", f"{github.repo}/releases/assets/{existing[name]['id']}")
    release = github.request("PATCH", f"{github.repo}/releases/{release['id']}", {
        "body": body, "draft": draft, "prerelease": prerelease,
    })
    return {**result, "url": release["html_url"], "published": str(not release["draft"]).lower()}
