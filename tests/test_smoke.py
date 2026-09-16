"""Smoke tests for site-context-mcp (mocked HTTP; no live network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from site_context_mcp import cache as cache_mod
from site_context_mcp.server import get_page, get_project, list_site_pages, search_site

LLMS_TXT = """# YongBo Yu

> Toronto AI engineer focused on agent systems.

## Core pages

- [Home](https://yongbo-yu.vercel.app/): positioning
- [Who Is YongBo Yu?](https://yongbo-yu.vercel.app/about-yongbo-yu): canonical profile
- [KiloDock](https://yongbo-yu.vercel.app/projects/kilodock): gym operating system project evidence
- [Agent systems](https://yongbo-yu.vercel.app/agent-systems)
- [resume.json](https://yongbo-yu.vercel.app/resume.json)

## Notes

- [Why Agent Systems Fail](https://yongbo-yu.vercel.app/essays/why-agent-systems-fail-in-production)
"""

KILODOCK_HTML = """<!doctype html>
<html lang="en">
<head>
  <title>KiloDock — Gym Operating System by YongBo Yu | YongBo Yu</title>
  <meta name="description" content="KiloDock is a multi-tenant CrossFit gym operating system independently built by YongBo Yu." />
</head>
<body>
  <nav>Home About</nav>
  <main>
    <article>
      <h1>KiloDock — Gym Operating System by YongBo Yu</h1>
      <p>KiloDock is a multi-tenant CrossFit gym operating system independently built by YongBo Yu.</p>
      <p>Stack: React, React Native, FastAPI, PostgreSQL, Supabase, and a Gemini programming copilot.</p>
      <p>Mobile schedule latency reduced to 683ms. Admin load reduced to 778ms.</p>
    </article>
  </main>
</body>
</html>
"""

ABOUT_HTML = """<!doctype html>
<html><head><title>Who Is YongBo Yu?</title>
<meta name="description" content="YongBo Yu is a Toronto AI engineer." />
</head><body><main><article>
<p>YongBo Yu, also known as Yong Yu, focuses on agent systems and LLM workflows.</p>
</article></main></body></html>
"""

HOME_HTML = """<!doctype html>
<html><head><title>YongBo Yu</title></head>
<body><main><p>Selected work: KiloDock, TradingAgents, Enterprise RAG.</p></main></body></html>
"""

AGENT_HTML = """<!doctype html>
<html><head><title>Agent systems</title></head>
<body><main><p>Notes on agent systems, tool routing, and evals.</p></main></body></html>
"""

ESSAY_HTML = """<!doctype html>
<html><head><title>Why Agent Systems Fail in Production</title></head>
<body><main><p>State drift, tool risk, missing evals, and single-model outages.</p></main></body></html>
"""

RESUME_JSON = {
    "basics": {"name": "YongBo Yu", "label": "AI Engineer"},
    "projects": [
        {
            "name": "KiloDock",
            "description": "CrossFit gym operating system (demo).",
            "url": "https://yongbo-yu.vercel.app/projects/kilodock",
            "highlights": ["FastAPI booking", "683ms mobile schedule"],
            "startDate": "2025-10",
        }
    ],
}


def _response(url: str, text: str, content_type: str, status: int = 200) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(
        status_code=status,
        headers={"content-type": content_type},
        text=text,
        request=request,
    )


class FakeClient:
    """Minimal httpx.Client stand-in for corpus fetches."""

    def __init__(self, mapping: dict[str, httpx.Response], *args: Any, **kwargs: Any) -> None:
        self._mapping = mapping

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        # Normalize trailing slash variants used by seed URLs.
        key = url.rstrip("/") if url.rstrip("/") in self._mapping else url
        if key not in self._mapping and url not in self._mapping:
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request, text="missing")
        return self._mapping.get(key) or self._mapping[url]


@pytest.fixture
def mock_corpus(monkeypatch: pytest.MonkeyPatch):
    mapping = {
        "https://yongbo-yu.vercel.app/llms.txt": _response(
            "https://yongbo-yu.vercel.app/llms.txt", LLMS_TXT, "text/plain; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app": _response(
            "https://yongbo-yu.vercel.app/", HOME_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/": _response(
            "https://yongbo-yu.vercel.app/", HOME_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/about-yongbo-yu": _response(
            "https://yongbo-yu.vercel.app/about-yongbo-yu", ABOUT_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/projects/kilodock": _response(
            "https://yongbo-yu.vercel.app/projects/kilodock",
            KILODOCK_HTML,
            "text/html; charset=utf-8",
        ),
        "https://yongbo-yu.vercel.app/agent-systems": _response(
            "https://yongbo-yu.vercel.app/agent-systems", AGENT_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/essays/why-agent-systems-fail-in-production": _response(
            "https://yongbo-yu.vercel.app/essays/why-agent-systems-fail-in-production",
            ESSAY_HTML,
            "text/html; charset=utf-8",
        ),
        "https://yongbo-yu.vercel.app/resume.json": _response(
            "https://yongbo-yu.vercel.app/resume.json",
            json.dumps(RESUME_JSON),
            "application/json",
        ),
    }

    def factory(*args: Any, **kwargs: Any) -> FakeClient:
        return FakeClient(mapping, *args, **kwargs)

    monkeypatch.setattr(cache_mod.httpx, "Client", factory)
    cache_mod.cache.llms_text = None
    cache_mod.cache.pages = {}
    cache_mod.cache.resume = None
    cache_mod.cache.fetched_at = None
    cache_mod.cache.errors = []
    yield mapping


@pytest.fixture
def mock_partial_failure(monkeypatch: pytest.MonkeyPatch):
    """llms.txt ok; one linked page fails — cache_notes should surface it."""

    mapping = {
        "https://yongbo-yu.vercel.app/llms.txt": _response(
            "https://yongbo-yu.vercel.app/llms.txt", LLMS_TXT, "text/plain; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app": _response(
            "https://yongbo-yu.vercel.app/", HOME_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/": _response(
            "https://yongbo-yu.vercel.app/", HOME_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/about-yongbo-yu": _response(
            "https://yongbo-yu.vercel.app/about-yongbo-yu", ABOUT_HTML, "text/html; charset=utf-8"
        ),
        "https://yongbo-yu.vercel.app/projects/kilodock": _response(
            "https://yongbo-yu.vercel.app/projects/kilodock",
            KILODOCK_HTML,
            "text/html; charset=utf-8",
        ),
        "https://yongbo-yu.vercel.app/agent-systems": _response(
            "https://yongbo-yu.vercel.app/agent-systems",
            "boom",
            "text/plain",
            status=500,
        ),
        "https://yongbo-yu.vercel.app/essays/why-agent-systems-fail-in-production": _response(
            "https://yongbo-yu.vercel.app/essays/why-agent-systems-fail-in-production",
            ESSAY_HTML,
            "text/html; charset=utf-8",
        ),
        "https://yongbo-yu.vercel.app/resume.json": _response(
            "https://yongbo-yu.vercel.app/resume.json",
            json.dumps(RESUME_JSON),
            "application/json",
        ),
    }

    class RaisingClient(FakeClient):
        def get(self, url: str) -> httpx.Response:
            resp = super().get(url)
            if resp.status_code >= 400:
                resp.raise_for_status()
            return resp

    def factory(*args: Any, **kwargs: Any) -> RaisingClient:
        return RaisingClient(mapping, *args, **kwargs)

    monkeypatch.setattr(cache_mod.httpx, "Client", factory)
    cache_mod.cache.llms_text = None
    cache_mod.cache.pages = {}
    cache_mod.cache.resume = None
    cache_mod.cache.fetched_at = None
    cache_mod.cache.errors = []
    yield


def test_list_site_pages_includes_sources(mock_corpus):
    data = json.loads(list_site_pages())
    assert data["page_count"] >= 4
    paths = {p["path"] for p in data["pages"]}
    assert "/llms.txt" in paths
    assert "/projects/kilodock" in paths
    assert "sources" in data
    assert any(u.endswith("/llms.txt") for u in data["sources"])
    assert data["cache_notes"] is None


def test_get_page_by_path(mock_corpus):
    data = json.loads(get_page("/projects/kilodock"))
    assert data["path"] == "/projects/kilodock"
    assert "KiloDock" in data["title"]
    assert "683ms" in data["text"]
    assert data["sources"]
    assert any(u.endswith("/projects/kilodock") for u in data["sources"])


def test_get_page_by_url(mock_corpus):
    data = json.loads(get_page("https://yongbo-yu.vercel.app/about-yongbo-yu"))
    assert data["path"] == "/about-yongbo-yu"
    assert "agent systems" in data["text"].lower()


def test_search_site_returns_snippets_and_sources(mock_corpus):
    data = json.loads(search_site("KiloDock gym FastAPI"))
    assert data["match_count"] >= 1
    assert all("snippet" in m and "source" in m for m in data["matches"])
    assert any("KiloDock" in m["snippet"] or "kilodock" in m["source"] for m in data["matches"])
    assert data["sources"]
    assert any("yongbo-yu.vercel.app" in u for u in data["sources"])


def test_get_project_kilodock(mock_corpus):
    data = json.loads(get_project("kilodock"))
    assert data["slug"] == "kilodock"
    assert data["url"].endswith("/projects/kilodock")
    assert "KiloDock" in (data["name"] or "")
    assert data.get("resume_cross_link") is not None
    assert data["resume_cross_link"]["name"] == "KiloDock"
    assert any(u.endswith("/projects/kilodock") for u in data["sources"])
    assert any(u.endswith("/resume.json") for u in data["sources"])


def test_cache_notes_on_partial_failure(mock_partial_failure):
    data = json.loads(list_site_pages())
    assert data["sources"]
    assert data["cache_notes"]
    assert any("agent-systems" in note for note in data["cache_notes"])


def test_search_empty_query(mock_corpus):
    data = json.loads(search_site("  "))
    assert data["matches"] == []
    assert "error" in data
    assert "sources" in data
