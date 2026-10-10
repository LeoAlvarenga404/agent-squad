"""Vercel deploy adapter. The runner (code), never the model, deploys previews.

A deploy adapter provides:
    deploy(site_dir: Path, slug: str) -> str
        Publishes a PREVIEW of the site, makes it publicly viewable and returns its https URL. Raises RuntimeError
        on failure. Must never publish to production.
    public_url(site_dir: Path) -> str | None
        The project's stable public URL if it opens without login, else None. Called after the first build only.

squad.toml: [vercel] scope = "my-team"  (empty = the CLI's default scope)
"""
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from ...config import child_env, settings

URL_RE = re.compile(r"https://[a-z0-9-]+\.vercel\.app")
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")


def _vercel(site_dir: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("vercel")
    if not exe:
        raise RuntimeError("vercel CLI not found")
    scope = settings().section("vercel").get("scope", "")
    return subprocess.run([exe, *args, *(["--scope", scope] if scope else [])], cwd=site_dir, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=900,
                          env=child_env("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"))


def _project(site_dir: Path) -> str | None:
    try:
        name = json.loads((Path(site_dir) / ".vercel" / "project.json").read_text(encoding="utf-8"))["projectName"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return name if isinstance(name, str) and PROJECT_RE.fullmatch(name) else None


def _opens(url: str) -> bool:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15)
        return r.status == 200 and r.geturl().rstrip("/") == url.rstrip("/")
    except Exception:
        return False


def deploy(site_dir: Path, slug: str) -> str:
    r = _vercel(site_dir, "deploy", "--yes")
    urls = URL_RE.findall(r.stdout)
    if r.returncode != 0 or not urls:
        raise RuntimeError(f"vercel deploy failed ({r.returncode}): {(r.stderr or r.stdout)[-300:]}")
    project = _project(site_dir)
    if project:
        _vercel(site_dir, "project", "protection", "disable", project, "--sso")
    if not _opens(urls[-1]):
        raise RuntimeError(f"preview is not publicly reachable: {urls[-1]}")
    return urls[-1]


def public_url(site_dir: Path) -> str | None:
    project = _project(site_dir)
    url = f"https://{project}.vercel.app" if project else ""
    return url if URL_RE.fullmatch(url) and _opens(url) else None
