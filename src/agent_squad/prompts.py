"""Prompt templates: {{business}}, {{owner}}, {{language}} and {{workspace}} come from squad.toml."""
from pathlib import Path

from .config import ROOT, settings


def render(text: str, **extra: str) -> str:
    s = settings()
    values = {"business": s.business.name, "owner": s.business.owner, "language": s.business.language,
              "workspace": str(s.workspace), **extra}
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def load(relative: str, **extra: str) -> str:
    """Renders a file under agents/ (e.g. 'sales/SOUL.md')."""
    return render((ROOT / "agents" / relative).read_text(encoding="utf-8"), **extra)


def read(path: Path, **extra: str) -> str:
    return render(Path(path).read_text(encoding="utf-8"), **extra)
