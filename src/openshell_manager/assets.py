"""Canonical, immutable package assets (wheels are installed unpacked)."""
from importlib.resources import files

ROOT = files("openshell_manager")
RESOURCES = ROOT / "resources"
