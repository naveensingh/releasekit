import json


MARKER = ".fossify/release-marker.txt"


def load(root, filename=".releasekit.json"):
    config = json.loads((root / filename).read_text())
    if not config.get("version_files"):
        raise ValueError("Configure at least one version_files entry in .releasekit.json")
    config.setdefault("changelog", "CHANGELOG.md")
    config.setdefault("tag_prefix", "v")
    config.setdefault("release_branch", "release/next")
    config.setdefault("prepare_paths", [])
    return config


def project_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Project path escapes the repository: {name}")
    return path
