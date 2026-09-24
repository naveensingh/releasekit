import re


HEADING = re.compile(r"(?m)^##\s+\[?([^\]\s]+)\]?(?:[^\r\n]*)$")
LINK = re.compile(r"(?m)^\[([^\]]+)\]:\s*(.+)$")


def section(text, name):
    headings = list(HEADING.finditer(text))
    matches = [(i, h) for i, h in enumerate(headings) if h[1].lower() == name.lower()]
    if len(matches) != 1:
        raise ValueError(f"Expected one ## [{name}] section in the changelog")
    i, heading = matches[0]
    end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
    links = LINK.search(text, heading.end(), end)
    if links:
        end = links.start()
    return heading, end, text[heading.end():end].strip()


def has_entries(notes):
    without_comments = re.sub(r"<!--.*?-->", "", notes, flags=re.S)
    return any(line.strip() and not line.lstrip().startswith("#") for line in without_comments.splitlines())


def release_notes(text, version):
    _, _, notes = section(text, version)
    if not has_entries(notes):
        raise ValueError(f"No release notes for {version}")
    links = "\n".join(m[0] for m in LINK.finditer(text))
    return notes + ("\n\n" + links if links else "") + "\n"


def prepare_changelog(text, version, date, repository, prefix, previous_tag):
    heading, _, notes = section(text, "Unreleased")
    if not has_entries(notes):
        return None
    if any(h[1] == version for h in HEADING.finditer(text)):
        raise ValueError(f"Changelog already contains {version}")
    updated = text[:heading.end()] + f"\n\n## [{version}] - {date}" + text[heading.end():]
    tag = prefix + version
    base = f"https://github.com/{repository}"
    replacements = {
        "Unreleased": f"{base}/compare/{tag}...HEAD",
        version: f"{base}/compare/{previous_tag}...{tag}" if previous_tag else f"{base}/releases/tag/{tag}",
    }
    for label, url in replacements.items():
        pattern = rf"(?mi)^\[{re.escape(label)}\]:.*$"
        line = f"[{label}]: {url}"
        if re.search(pattern, updated):
            updated = re.sub(pattern, lambda _: line, updated)
        else:
            updated = updated.rstrip() + "\n\n" + line + "\n"
    return updated


def fastlane_notes(notes):
    notes = LINK.sub("", notes)
    notes = re.sub(r"(?m)^###\s+(.+)$", r"\1:", notes)
    notes = re.sub(r"(?m)^[-*]\s+", "• ", notes)
    notes = re.sub(r"\s*\(\[?#\d+\]?(?:\([^)]*\))?\)", "", notes)
    return notes.strip() + "\n"
