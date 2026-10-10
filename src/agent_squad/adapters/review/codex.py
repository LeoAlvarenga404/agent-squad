"""Review adapter: the OpenAI Codex CLI as an independent, read-only reviewer (another model family on purpose).

A review adapter module provides:
    review(ticket: dict, round: int, out_dir: Path) -> dict | None
        `ticket` has id, slug and preview_url; `out_dir` is a fresh folder for screenshots, logs and the verdict.
        Returns {"verdict": "APPROVED" | "CHANGES", "text": str, "file": Path} where `file` holds `text` (the
        builder's fix session reads it from disk, never from its prompt), or None when no review could run.
        CHANGES means there are HIGH or MEDIUM items to fix.

Codex runs in agents/reviewer, whose AGENTS.md holds the checklist and the answer format, with desktop and mobile
screenshots of the preview taken by Playwright (through npx).
"""
import re
import shutil
import subprocess
from pathlib import Path

from ...config import ROOT, child_env, settings

REVIEWER = ROOT / "agents" / "reviewer"
VERDICT_RE = re.compile(r"VERDICT:\s*(APPROVED|CHANGES)")
VIEWPORTS = (("desktop", "1440, 900"), ("mobile", "390, 844"))
PLAYWRIGHT = "playwright@1.48.2"


def screenshot(url: str, size: str, path: Path) -> bool:
    """Full-page screenshot; installs Playwright's Chromium on the first miss."""
    npx = shutil.which("npx")
    if not npx or not url.startswith("https://"):
        return False
    cmd = [npx, "-y", PLAYWRIGHT, "screenshot", "--full-page", "--viewport-size", size, "--", url, str(path)]
    for attempt in range(2):
        try:
            subprocess.run(cmd, capture_output=True, timeout=240, env=child_env())
            if path.exists():
                return True
            if attempt == 0:
                subprocess.run([npx, "-y", PLAYWRIGHT, "install", "chromium"], capture_output=True, timeout=900,
                               env=child_env())
        except subprocess.TimeoutExpired:
            return False
    return False


def review(ticket: dict, round: int, out_dir: Path) -> dict | None:
    codex = shutil.which("codex")
    if not codex or not ticket.get("preview_url"):
        return None
    s = settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = [out_dir / f"{name}.png" for name, size in VIEWPORTS
             if screenshot(ticket["preview_url"], size, out_dir / f"{name}.png")]
    out = out_dir / "review.txt"
    out.unlink(missing_ok=True)
    cmd = [codex, "exec", "-C", str(REVIEWER), "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "-o", str(out)]
    for p in shots:
        cmd += ["-i", str(p)]
    slug = ticket["slug"]
    request = (f"Ticket {ticket['id']}, slug {slug}. Site code: {s.workspace / 'sites' / slug}. Dossier: "
               f"{s.workspace / 'leads' / slug / 'dossier.json'}. Preview: {ticket['preview_url']}. Business: "
               f"{s.business.name}. Site copy language: {s.business.language}. "
               + ("The attached screenshots are the preview (desktop and mobile). " if shots
                  else "No screenshots (capture failed). ")
               + "Review following your AGENTS.md and answer in its format.")
    try:
        with open(out_dir / "codex.log", "w", encoding="utf-8") as log:  # `squad logs codex` follows it live
            subprocess.run(cmd, input=request, text=True, encoding="utf-8", errors="replace", stdout=log,
                           stderr=subprocess.STDOUT, timeout=s.limits.builder_timeout_min * 60,
                           env=child_env("CODEX_HOME"))
    except subprocess.TimeoutExpired:
        return None
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    m = VERDICT_RE.search(text)
    return {"verdict": m[1], "text": text.strip(), "file": out} if m else None
