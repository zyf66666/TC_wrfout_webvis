"""The public distribution contains the dashboard and data tools only."""
from pathlib import Path
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from wrf_webvis.app.main import app
from wrf_webvis.app.storage.user_store import PAGES

ROOT = Path(__file__).resolve().parents[2]


def test_public_app_excludes_optional_api_routes():
    prefixes = ("/api/assistant", "/api/literature", "/api/schematics", "/schematics", "/schematic-engine")
    paths = [*app.openapi()["paths"], *(getattr(route, "path", "") for route in app.routes)]
    assert not [path for path in paths if path.startswith(prefixes)]


def test_public_account_pages_only_offer_data():
    assert set(PAGES) == {"data"}


@pytest.mark.parametrize("name", ["ai_research", "wrf_webvis/app/static/primer", "wrf_webvis/app/services/llm", "wrf_webvis/assistant_prompt.md"])
def test_excluded_modules_are_not_distributed(name):
    assert not (ROOT / name).exists()


def test_core_endpoints_still_registered():
    paths = set(app.openapi()["paths"])
    assert {"/api/dashboards", "/api/plots/dry-run", "/api/data/catalog", "/api/processing/v3/plan"} <= paths


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
        self.views = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-view" in attrs:
            self.views.append(attrs["data-view"])
        if tag == "script" and attrs.get("src"):
            self.refs.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.refs.append(attrs["href"])


def test_public_page_has_core_navigation_and_resolvable_assets():
    parser = Assets()
    html = (ROOT / "wrf_webvis/app/static/index.html").read_text()
    parser.feed(html)
    assert parser.views == ["dashboardView", "dataView"]
    assert "openAISettings" not in html
    assert "问 AI" not in (ROOT / "wrf_webvis/app/static/app.js").read_text()
    with TestClient(app, client=("127.0.0.1", 53123)) as client:
        for url in parser.refs:
            assert client.get(url).status_code == 200, url
