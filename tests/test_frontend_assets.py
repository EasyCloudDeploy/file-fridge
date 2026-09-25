"""Tests for Vite frontend asset helpers."""

import json
from unittest.mock import MagicMock

import pytest
from markupsafe import Markup

from app import frontend_assets


@pytest.mark.unit
class TestFrontendAssets:
    def test_load_manifest_missing_returns_empty(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(frontend_assets, "MANIFEST_PATH", tmp_path / "missing-manifest.json")
        assert frontend_assets._load_manifest() == {}

    def test_load_manifest_invalid_json_raises(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_manifest = tmp_path / "manifest.json"
        bad_manifest.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(frontend_assets, "MANIFEST_PATH", bad_manifest)

        with pytest.raises(RuntimeError, match="Failed to load Vite manifest"):
            frontend_assets._load_manifest()

    def test_resolve_entry_aliases(self) -> None:
        assert frontend_assets._resolve_entry("app") == "frontend/entries/app.js"
        assert frontend_assets._resolve_entry("custom.js") == "custom.js"

    def test_vite_assets_empty_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(frontend_assets, "_load_manifest", dict)
        assert frontend_assets.vite_assets("app") == Markup("")

    def test_vite_assets_renders_css_and_js_with_imports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = {
            "frontend/entries/app.js": {
                "file": "assets/app-abc123.js",
                "css": ["assets/app-abc123.css"],
                "imports": ["frontend/chunks/vendor.js"],
            },
            "frontend/chunks/vendor.js": {
                "file": "assets/vendor-def456.js",
                "css": [],
                "imports": [],
            },
        }
        monkeypatch.setattr(frontend_assets, "_load_manifest", lambda: manifest)

        html = str(frontend_assets.vite_assets("app"))
        assert '<link rel="stylesheet" href="/static/dist/assets/app-abc123.css">' in html
        assert '<script type="module" src="/static/dist/assets/vendor-def456.js"></script>' in html
        assert '<script type="module" src="/static/dist/assets/app-abc123.js"></script>' in html

    def test_vite_assets_deduplicates_shared_imports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = {
            "frontend/entries/app.js": {
                "file": "assets/app.js",
                "imports": ["frontend/chunks/shared.js"],
            },
            "frontend/entries/grid.js": {
                "file": "assets/grid.js",
                "imports": ["frontend/chunks/shared.js"],
            },
            "frontend/chunks/shared.js": {"file": "assets/shared.js"},
        }
        monkeypatch.setattr(frontend_assets, "_load_manifest", lambda: manifest)

        html = str(frontend_assets.vite_assets("app", "grid"))
        assert html.count("assets/shared.js") == 1

    def test_configure_templates_registers_global(self) -> None:
        templates = MagicMock()
        templates.env.globals = {}

        result = frontend_assets.configure_templates(templates)
        assert result is templates
        assert templates.env.globals["vite_assets"] is frontend_assets.vite_assets

    def test_load_manifest_success(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest_path = tmp_path / "manifest.json"
        payload = {"frontend/entries/app.js": {"file": "assets/app.js"}}
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(frontend_assets, "MANIFEST_PATH", manifest_path)

        assert frontend_assets._load_manifest() == payload
