"""The recipe registry. Recipes register themselves when their module is imported; `get` and `all_recipes` import
every module of this package first."""
import difflib

from .base import Param, Recipe, VARY_MODES  # noqa: F401

REGISTRY = {}

_MODULES = ('ui', 'retro', 'impact', 'scifi', 'magic', 'ambience')


def register(recipe):
    """Add a recipe (a later one of the same name replaces the earlier); returns it."""
    REGISTRY[recipe.name] = recipe
    return recipe


def _load():
    import importlib
    for name in _MODULES:
        importlib.import_module(f'{__name__}.{name}')


def all_recipes():
    _load()
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def nearest(name):
    _load()
    match = difflib.get_close_matches(name, list(REGISTRY), n=1, cutoff=0.5)
    return match[0] if match else None


def get(name):
    _load()
    if name not in REGISTRY:
        near = nearest(name)
        hint = f"; did you mean '{near}'?" if near else '; list them with: sfx.py recipes'
        raise KeyError(f"unknown recipe '{name}'{hint}")
    return REGISTRY[name]
