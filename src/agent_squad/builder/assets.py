"""Stock photos for the builder, as MCP tools: the API key stays in this server and downloads only reach allowed
hosts and the current ticket's site folder. The builder has no curl, so this is its only way to fetch images."""
import json
import os
import re
import urllib.parse
import urllib.request

from .. import db
from ..config import settings
from ..crm.tickets import own_ticket

MAX_BYTES = 10 * 1024 * 1024
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,60}\.(?:jpe?g|png|webp)")


def _hosts() -> set[str]:
    return set(settings().section("builder").get("image_hosts", ["images.pexels.com"]))


def search_photos(query: str, n: int = 5) -> list[dict]:
    """Searches stock photos (Pexels) for the site. Returns [{url, alt, photographer, page}]; save one with save_photo.
    n between 1 and 15."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise ValueError("PEXELS_API_KEY is not configured: build without stock photos and list it as pending")
    if not 1 <= n <= 15 or not query.strip():
        raise ValueError("query required and n must be 1 to 15")
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": query[:100], "per_page": n})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": key}), timeout=20) as r:
        photos = json.load(r).get("photos", [])
    return [{"url": p["src"]["large2x"], "alt": p.get("alt", ""), "photographer": p.get("photographer", ""),
             "page": p.get("url", "")} for p in photos]


def save_photo(url: str, filename: str) -> dict:
    """Downloads a photo found with search_photos into public/images/<filename> of the current ticket's site.
    filename: lowercase, e.g. 'hero-salon.jpg'. Record it in public/images/PROVENANCE.md."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _hosts():
        raise ValueError(f"only https images from {', '.join(sorted(_hosts()))}")
    if not NAME_RE.fullmatch(filename):
        raise ValueError("invalid filename (lowercase letters, digits and dashes, .jpg/.png/.webp)")
    with db.connect() as c:
        slug = c.execute("select slug from tickets where id=%s", (own_ticket(),)).fetchone()["slug"]
    dest = settings().workspace / "sites" / slug / "public" / "images"
    with urllib.request.urlopen(url, timeout=30) as r:
        if urllib.parse.urlparse(r.geturl()).hostname not in _hosts():
            raise ValueError("redirected to a host that is not allowed")
        if not r.headers.get("Content-Type", "").startswith("image/"):
            raise ValueError("not an image")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("image larger than 10 MB")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / filename).write_bytes(data)
    return {"path": f"public/images/{filename}", "bytes": len(data)}
