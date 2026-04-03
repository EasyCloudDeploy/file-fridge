"""Helpers for serving Vite-built frontend assets from Jinja templates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "static/dist/manifest.json"
ENTRY_ALIASES = {
    "app": "frontend/entries/app.js",
    "grid": "frontend/entries/grid.js",
    "charts": "frontend/entries/charts.js",
}


def _load_manifest() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to load Vite manifest at {MANIFEST_PATH}: {exc}") from exc


def _resolve_entry(entry: str) -> str:
    return ENTRY_ALIASES.get(entry, entry)


def vite_assets(*entries: str) -> Markup:
    """Render CSS and JS tags for one or more Vite entrypoints."""
    manifest = _load_manifest()
    css_files: list[str] = []
    js_files: list[str] = []
    seen_manifest_keys: set[str] = set()

    def add_file(target_list: list[str], path: str) -> None:
        if path not in target_list:
            target_list.append(path)

    def collect(manifest_key: str) -> None:
        if manifest_key in seen_manifest_keys:
            return
        seen_manifest_keys.add(manifest_key)

        item = manifest.get(manifest_key)
        if not item:
            return

        for imported_key in item.get("imports", []):
            collect(imported_key)

        for css_file in item.get("css", []):
            add_file(css_files, css_file)

        if item.get("file"):
            add_file(js_files, item["file"])

    for entry in entries:
        collect(_resolve_entry(entry))

    if not css_files and not js_files:
        return Markup("")

    tags: list[str] = []
    for css_file in css_files:
        href = escape(f"/static/dist/{css_file}")
        tags.append(f'<link rel="stylesheet" href="{href}">')
    for js_file in js_files:
        src = escape(f"/static/dist/{js_file}")
        tags.append(f'<script type="module" src="{src}"></script>')

    return Markup("\n".join(tags))


def configure_templates(templates: Jinja2Templates) -> Jinja2Templates:
    """Register frontend helper globals on a Jinja environment."""
    templates.env.globals["vite_assets"] = vite_assets
    return templates
