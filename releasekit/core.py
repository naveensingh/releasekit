from datetime import datetime, timezone
import os
import re
import subprocess

from .changelog import fastlane_notes, has_entries, prepare_changelog, release_notes, section
from .config import MARKER, load, project_path
from .versions import bump_version, parse_version, property_value, read_version, version_edits, version_order


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def repository_name(root, supplied=""):
    if supplied:
        name = supplied
    else:
        remote = git(root, "remote", "get-url", "origin")
        match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
        if not match:
            raise ValueError("Pass --repository OWNER/REPO when origin is not a GitHub URL")
        name = match[1]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        raise ValueError("Repository must be OWNER/REPO")
    return name


def prepare(root, config_path, repository, output_dir, explicit_version=""):
    config = load(root, config_path)
    changelog_path = project_path(root, config["changelog"])
    changelog = changelog_path.read_text()
    if not has_entries(section(changelog, "Unreleased")[2]):
        return {"changed": "false", "branch": config["release_branch"], "reason": "No unreleased changelog entries"}

    current = read_version(root, config["version_files"][0])
    for spec in config["version_files"][1:]:
        if read_version(root, spec) != current:
            raise ValueError("Configured version files disagree; synchronize them before preparing a release")
    prefix = config["tag_prefix"]
    reachable_tags = git(root, "tag", "--merged", "HEAD").splitlines()
    tagged_versions = {}
    for tag in reachable_tags:
        if tag.startswith(prefix):
            try:
                parse_version(tag[len(prefix):])
                tagged_versions[tag[len(prefix):]] = tag
            except ValueError:
                pass
    previous_tag = tagged_versions.get(current)
    if tagged_versions and not previous_tag:
        raise ValueError(f"Current version {current} has no reachable {prefix}{current} tag; reconcile the release history first")
    if explicit_version:
        parse_version(explicit_version)
        version = explicit_version
    elif previous_tag:
        commits = git(root, "log", "--format=%B", f"{previous_tag}..HEAD")
        version = bump_version(current, commits)
    else:
        version = current
    tag = prefix + version
    if tag in git(root, "tag", "--list").splitlines():
        raise ValueError(f"Tag {tag} already exists; choose an unreleased version")
    if previous_tag and version_order(version) <= version_order(current):
        raise ValueError("The next version must be newer than the current release")

    edits = {}
    for spec in config["version_files"]:
        edits.update(version_edits(root, spec, version, edits))
    date = datetime.now(timezone.utc).date().isoformat()
    updated = prepare_changelog(changelog, version, date, repository, prefix, previous_tag)
    edits[changelog_path] = updated
    edits[project_path(root, MARKER)] = version + "\n"
    notes = release_notes(updated, version)
    if config.get("android_changelogs"):
        spec = next((s for s in config["version_files"] if s.get("increment")), None)
        if not spec or spec["type"] != "properties":
            raise ValueError("android_changelogs needs a properties version file with an increment key")
        code = property_value(edits[project_path(root, spec["path"])], spec["increment"])
        edits[project_path(root, f"{config['android_changelogs']}/{code}.txt")] = fastlane_notes(notes)
    for path, content in edits.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if config.get("prepare_command"):
        subprocess.run(
            config["prepare_command"], shell=True, check=True, cwd=root,
            env={**os.environ, "RELEASEKIT_VERSION": version, "RELEASEKIT_TAG": tag},
        )
    for spec in config["version_files"]:
        if read_version(root, spec) != version:
            raise ValueError(f"Prepare command changed the release version in {spec['path']}")
    body = output_dir / "release-notes.md"
    body.write_text(release_notes(changelog_path.read_text(), version))
    paths = {str(path.relative_to(root)) for path in edits}
    paths.update(config["prepare_paths"])
    return {
        "changed": "true", "version": version, "tag": tag,
        "branch": config["release_branch"], "body-path": str(body),
        "paths": "\n".join(sorted(paths)),
    }


def inspect_release(root, config_path, output_dir):
    config = load(root, config_path)
    values = [line.strip() for line in project_path(root, MARKER).read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    if len(values) != 1:
        raise ValueError(f"Expected one version in {MARKER}")
    version = values[0]
    parse_version(version)
    for spec in config["version_files"]:
        if read_version(root, spec) != version:
            raise ValueError(f"{spec['path']} does not match release marker {version}")
    notes = release_notes(project_path(root, config["changelog"]).read_text(), version)
    notes_path = output_dir / "release-notes.md"
    notes_path.write_text(notes)
    return {
        "version": version, "tag": config["tag_prefix"] + version,
        "source-sha": git(root, "rev-parse", "HEAD"),
        "body-path": str(notes_path),
    }
