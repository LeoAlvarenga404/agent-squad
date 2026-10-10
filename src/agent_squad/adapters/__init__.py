"""Swappable integrations. squad.toml picks one per kind under [adapters]:

    deploy = "vercel"            -> agent_squad.adapters.deploy.vercel
    review = "my_pkg.my_review"  -> any importable module with the same functions

See docs/adapters.md for the functions each kind must provide.
"""
import importlib

from ..config import settings


def load(kind: str, name: str | None = None):
    name = name or getattr(settings().adapters, kind)
    module = name if "." in name else f"agent_squad.adapters.{kind}.{name}"
    return importlib.import_module(module)
