import json
import re
import tomllib

from .config import project_path


SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def parse_version(value):
    match = SEMVER.fullmatch(value)
    if not match:
        raise ValueError(f"Expected a semantic version, got {value!r}")
    prerelease = match[4]
    if prerelease and any(p.isdigit() and len(p) > 1 and p[0] == "0" for p in prerelease.split(".")):
        raise ValueError(f"Invalid semantic prerelease: {value}")
    return tuple(map(int, match.groups()[:3])), prerelease


def bump_version(current, commits):
    numbers, prerelease = parse_version(current)
    if prerelease:
        raise ValueError("Select the next prerelease or stable version explicitly with --version")
    major, minor, patch = numbers
    if re.search(r"(?m)^[\w-]+(?:\([^\n]*\))?!:|^BREAKING[ -]CHANGE:", commits):
        return f"{major + 1}.0.0"
    if re.search(r"(?m)^feat(?:\([^\n]*\))?:", commits):
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def version_order(value):
    numbers, prerelease = parse_version(value)
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in (prerelease or "").split("."))
    return numbers, prerelease is None, parts


def lookup(data, key):
    for part in key.split("."):
        data = data[part]
    return data


def assign(data, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        data = data[part]
    if parts[-1] not in data:
        raise ValueError(f"Missing version key: {key}")
    data[parts[-1]] = value


def property_value(text, key):
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*([^\r\n]+)", text)
    if not match:
        raise ValueError(f"Missing property: {key}")
    return match[1].strip()


def replace_property(text, key, value):
    pattern = rf"(?m)^(\s*{re.escape(key)}\s*=\s*)[^\r\n]+"
    result, count = re.subn(pattern, lambda m: m[1] + str(value), text)
    if count != 1:
        raise ValueError(f"Expected one {key} property, found {count}")
    return result


def replace_toml(text, key, value):
    parts = key.split(".")
    section, field = ".".join(parts[:-1]), parts[-1]
    active = ""
    count = 0
    lines = []
    for line in text.splitlines(keepends=True):
        header = re.match(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$", line.strip())
        if header:
            active = header[1].strip()
        if line.lstrip().startswith("[["):
            active = None
        if active == section:
            pattern = rf"^(\s*{re.escape(field)}\s*=\s*)([\"'])[^\"']*\2"
            line, n = re.subn(pattern, lambda m: m[1] + json.dumps(value), line)
            count += n
        lines.append(line)
    if count != 1:
        raise ValueError(f"Expected a string at TOML {key}; use a prepare_command for other layouts")
    result = "".join(lines)
    if lookup(tomllib.loads(result), key) != value:
        raise ValueError(f"Could not update TOML {key}")
    return result


def read_version(root, spec):
    text = project_path(root, spec["path"]).read_text()
    kind = spec["type"]
    if kind == "text":
        value = text.strip()
    elif kind in ("json", "npm"):
        value = lookup(json.loads(text), spec.get("key", "version"))
    elif kind in ("toml", "cargo"):
        value = lookup(tomllib.loads(text), spec.get("key", "package.version"))
    elif kind == "properties":
        value = property_value(text, spec.get("key", "VERSION_NAME"))
    else:
        raise ValueError(f"Unknown version file type: {kind}")
    parse_version(value)
    return value


def json_text(original, data):
    indentation = re.search(r"\n([ \t]+)\S", original)
    indent = indentation[1] if indentation else 2
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def version_edits(root, spec, version, pending=None):
    pending = pending or {}
    path = project_path(root, spec["path"])
    original = pending.get(path, path.read_text())
    kind = spec["type"]
    edits = {}
    if kind == "text":
        updated = version + "\n"
    elif kind in ("json", "npm"):
        data = json.loads(original)
        assign(data, spec.get("key", "version"), version)
        updated = json_text(original, data)
        if kind == "npm":
            for filename in ("package-lock.json", "npm-shrinkwrap.json"):
                lock = path.with_name(filename)
                if lock.exists():
                    text = pending.get(lock, lock.read_text())
                    locked = json.loads(text)
                    locked["version"] = version
                    if "" in locked.get("packages", {}):
                        locked["packages"][""]["version"] = version
                    edits[lock] = json_text(text, locked)
    elif kind in ("toml", "cargo"):
        updated = replace_toml(original, spec.get("key", "package.version"), version)
        if kind == "cargo":
            name = tomllib.loads(original)["package"]["name"]
            lock = project_path(root, spec.get("lockfile", str(path.relative_to(root).parent / "Cargo.lock")))
            if lock.exists():
                chunks = re.split(r"(?m)(?=^\[\[package\]\]\s*$)", pending.get(lock, lock.read_text()))
                count = 0
                for i, chunk in enumerate(chunks):
                    if chunk.startswith("[[package]]"):
                        package = tomllib.loads(chunk)["package"][0]
                        if package["name"] == name and "source" not in package:
                            chunks[i] = replace_property(chunk, "version", json.dumps(version))
                            count += 1
                if count != 1:
                    raise ValueError(f"Expected one local {name} package in {lock.name}")
                edits[lock] = "".join(chunks)
    elif kind == "properties":
        updated = replace_property(original, spec.get("key", "VERSION_NAME"), version)
        if spec.get("increment"):
            key = spec["increment"]
            updated = replace_property(updated, key, int(property_value(original, key)) + 1)
    else:
        raise ValueError(f"Unknown version file type: {kind}")
    edits[path] = updated
    return edits
