import hashlib
import json
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"GitHub API {status}: {message}")


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, repository, token, api_url="https://api.github.com"):
        if not token:
            raise ValueError("GITHUB_TOKEN is required to publish")
        self.base = api_url.rstrip("/")
        self.repo = f"/repos/{repository}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "releasekit",
        }
        self.opener = build_opener(Redirects())

    def request(self, method, path, payload=None, file=None):
        url = path if path.startswith(("https://", "http://")) else self.base + path
        headers = dict(self.headers)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if file:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(file.stat().st_size)
            data = file.open("rb")
        try:
            with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=300) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as error:
            try:
                message = json.loads(error.read()).get("message", error.reason)
            except (ValueError, AttributeError):
                message = error.reason
            finally:
                error.close()
            raise APIError(error.code, message) from error
        finally:
            if file:
                data.close()

    def get_release(self, tag):
        try:
            return self.request("GET", f"{self.repo}/releases/tags/{quote(tag, safe='')}")
        except APIError as error:
            if error.status == 404:
                page = 1
                while True:
                    releases = self.request("GET", f"{self.repo}/releases?per_page=100&page={page}")
                    found = next((r for r in releases if r["tag_name"] == tag), None)
                    if found or len(releases) < 100:
                        return found
                    page += 1
            raise

    def ensure_tag(self, tag, sha):
        path = f"{self.repo}/git/ref/tags/{quote(tag, safe='')}"
        try:
            obj = self.request("GET", path)["object"]
        except APIError as error:
            if error.status != 404:
                raise
            self.request("POST", f"{self.repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": sha})
            return
        while obj["type"] == "tag":
            obj = self.request("GET", f"{self.repo}/git/tags/{obj['sha']}")["object"]
        if obj["type"] != "commit" or obj["sha"] != sha:
            raise ValueError(f"Existing tag {tag} does not point to release commit {sha}")

    def assets(self, release_id):
        page = 1
        result = []
        while True:
            batch = self.request("GET", f"{self.repo}/releases/{release_id}/assets?per_page=100&page={page}")
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1

    def asset_digest(self, asset):
        digest = asset.get("digest", "") or ""
        if digest.startswith("sha256:") and asset.get("state") == "uploaded":
            return digest[7:]
        headers = {**self.headers, "Accept": "application/octet-stream"}
        request = Request(self.base + f"{self.repo}/releases/assets/{asset['id']}", headers=headers)
        with self.opener.open(request, timeout=300) as response:
            return hashlib.file_digest(response, "sha256").hexdigest()

    def upload(self, release, path):
        url = release["upload_url"].split("{")[0] + "?" + urlencode({"name": path.name})
        return self.request("POST", url, file=path)
