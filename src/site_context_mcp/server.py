"""stdio MCP server exposing public site/project context tools."""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from site_context_mcp.cache import (
    KILODOCK_URL,
    LLMS_TXT_URL,
    RESUME_JSON_URL,
    SITE_URL,
    SitePage,
    cache,
    project_slug_from_path,
)

mcp = FastMCP(
    "site-context-mcp",
    instructions=(
        "Answer questions about YongBo Yu's public website and projects using only "
        "the cached public site corpus (llms.txt and linked pages). Always cite the "
        "source URLs returned by each tool. Do not invent unpublished project claims "
        "or private app links. This server is for SITE/PROJECT context, not a resume summary."
    ),
)


def _sources(*urls: str) -> list[str]:
    seen: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.append(url)
    return seen


def _payload(data: dict[str, Any], *urls: str) -> str:
    body = {**data, "sources": _sources(*urls)}
    return json.dumps(body, ensure_ascii=False, indent=2)


def _page_summary(page: SitePage) -> dict[str, Any]:
    return {
        "url": page.url,
        "path": page.path,
        "title": page.title,
        "description": page.description,
        "kind": page.kind,
        "project_slug": project_slug_from_path(page.path),
    }


@mcp.tool()
def list_site_pages() -> str:
    """List cached public site pages discovered from llms.txt (paths, titles, URLs)."""
    pages = cache.require_corpus()
    items = sorted((_page_summary(p) for p in pages.values()), key=lambda x: x["path"])
    source_urls = [LLMS_TXT_URL] + [p["url"] for p in items]
    return _payload(
        {
            "host": SITE_URL,
            "page_count": len(items),
            "pages": items,
            "cache_notes": cache.errors or None,
        },
        *source_urls,
    )


@mcp.tool()
def get_page(path_or_url: str) -> str:
    """Fetch one cached public page by path (e.g. /about-yongbo-yu) or absolute HTTPS URL."""
    ref = (path_or_url or "").strip()
    if not ref:
        return _payload(
            {
                "error": "path_or_url must be a non-empty path or HTTPS URL on the wired host",
                "cache_notes": cache.errors or None,
            },
            LLMS_TXT_URL,
            SITE_URL,
        )

    page = cache.get_page_by_ref(ref)
    if page is None:
        cache.require_corpus()
        available = sorted({p.path for p in cache.pages.values()})
        return _payload(
            {
                "error": f"Page not found in cached corpus: {ref}",
                "available_paths": available,
                "cache_notes": cache.errors or None,
            },
            LLMS_TXT_URL,
            SITE_URL,
        )

    return _payload(
        {
            "url": page.url,
            "path": page.path,
            "title": page.title,
            "description": page.description,
            "kind": page.kind,
            "content_type": page.content_type,
            "text": page.text,
            "cache_notes": cache.errors or None,
        },
        page.url,
        LLMS_TXT_URL,
    )


@mcp.tool()
def search_site(query: str, limit: int = 8) -> str:
    """Keyword search over the cached site corpus; returns snippets with source URLs."""
    if not query or not query.strip():
        return _payload(
            {
                "query": query,
                "matches": [],
                "error": "query must be a non-empty string",
                "cache_notes": cache.errors or None,
            },
            LLMS_TXT_URL,
            SITE_URL,
        )

    pages = cache.require_corpus()
    limit = max(1, min(int(limit), 25))
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    matches: list[dict[str, Any]] = []

    def score_text(text: str) -> int:
        lower = text.lower()
        return sum(1 for t in tokens if t in lower)

    for page in pages.values():
        # Prefer paragraph / line-oriented chunks for snippets.
        chunks: list[str]
        if page.kind in {"llms", "text", "json"}:
            chunks = []
            for block in re.split(r"\n\s*\n", page.text):
                snippet = " ".join(block.split())
                if snippet:
                    chunks.append(snippet)
            if not chunks:
                chunks = [" ".join(page.text.split())]
        else:
            # HTML: sliding windows over sentences / long phrases.
            sentences = re.split(r"(?<=[.!?])\s+|\n+", page.text)
            chunks = [s.strip() for s in sentences if s and s.strip()]
            if len(chunks) < 2:
                # Fallback: overlapping word windows.
                words = page.text.split()
                window = 40
                chunks = [
                    " ".join(words[i : i + window])
                    for i in range(0, max(1, len(words)), window // 2)
                ]

        best_for_page: dict[str, Any] | None = None
        for chunk in chunks:
            if len(chunk) < 8:
                continue
            s = score_text(chunk)
            if not s:
                continue
            candidate = {
                "score": s,
                "snippet": chunk[:400],
                "source": page.url,
                "path": page.path,
                "title": page.title,
            }
            if best_for_page is None or candidate["score"] > best_for_page["score"]:
                best_for_page = candidate
            matches.append(candidate)

        # Also score title/description as matches.
        for field_name, value in (("title", page.title), ("description", page.description or "")):
            if not value:
                continue
            s = score_text(value)
            if s:
                matches.append(
                    {
                        "score": s + (1 if field_name == "title" else 0),
                        "snippet": value[:400],
                        "source": page.url,
                        "path": page.path,
                        "title": page.title,
                    }
                )

    matches.sort(key=lambda m: (-m["score"], m["source"], m["snippet"]))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in matches:
        key = f"{match['source']}|{match['snippet'][:120]}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)
        if len(deduped) >= limit:
            break

    source_urls = [LLMS_TXT_URL]
    for match in deduped:
        if match["source"] not in source_urls:
            source_urls.append(match["source"])

    return _payload(
        {
            "query": query,
            "match_count": len(deduped),
            "matches": deduped,
            "cache_notes": cache.errors or None,
        },
        *source_urls,
    )


@mcp.tool()
def get_project(name_or_slug: str = "kilodock") -> str:
    """Return public project evidence for a site project page (default: kilodock)."""
    ref = (name_or_slug or "kilodock").strip()
    if not ref:
        ref = "kilodock"

    pages = cache.require_corpus()
    slug = ref.lower().removeprefix("/projects/").strip("/")
    # Accept display names like "KiloDock".
    slug_compact = re.sub(r"[^a-z0-9]+", "", slug)

    project_page: SitePage | None = None
    for page in pages.values():
        page_slug = project_slug_from_path(page.path)
        if page_slug and (
            page_slug.lower() == slug
            or re.sub(r"[^a-z0-9]+", "", page_slug.lower()) == slug_compact
        ):
            project_page = page
            break
        if slug_compact and slug_compact in re.sub(r"[^a-z0-9]+", "", page.title.lower()):
            if page.path.startswith("/projects/"):
                project_page = page
                break

    if project_page is None:
        # Direct path attempt.
        project_page = cache.get_page_by_ref(f"/projects/{slug}")

    # Optional light cross-link from resume.json projects (not a resume clone).
    resume_project: dict[str, Any] | None = None
    if cache.resume:
        for project in cache.resume.get("projects") or []:
            pname = str(project.get("name") or "")
            if re.sub(r"[^a-z0-9]+", "", pname.lower()) == slug_compact:
                resume_project = {
                    "name": project.get("name"),
                    "description": project.get("description"),
                    "url": project.get("url"),
                    "highlights": (project.get("highlights") or [])[:5],
                    "startDate": project.get("startDate"),
                    "endDate": project.get("endDate"),
                }
                break

    if project_page is None and resume_project is None:
        available = sorted(
            {
                project_slug_from_path(p.path) or p.path
                for p in pages.values()
                if p.path.startswith("/projects/") or project_slug_from_path(p.path)
            }
        )
        return _payload(
            {
                "error": f"Project not found in cached corpus: {name_or_slug}",
                "available_projects": available,
                "cache_notes": cache.errors or None,
            },
            LLMS_TXT_URL,
            KILODOCK_URL,
        )

    sources = [LLMS_TXT_URL]
    if project_page:
        sources.append(project_page.url)
    if resume_project:
        sources.append(RESUME_JSON_URL)
        if resume_project.get("url"):
            sources.append(str(resume_project["url"]))

    return _payload(
        {
            "name": (resume_project or {}).get("name")
            or (project_page.title if project_page else slug),
            "slug": project_slug_from_path(project_page.path) if project_page else slug,
            "url": project_page.url if project_page else (resume_project or {}).get("url"),
            "path": project_page.path if project_page else None,
            "description": (project_page.description if project_page else None)
            or (resume_project or {}).get("description"),
            "text": project_page.text if project_page else None,
            "resume_cross_link": resume_project,
            "cache_notes": cache.errors or None,
        },
        *sources,
    )


def main() -> None:
    """Run the MCP server over stdio (default FastMCP transport)."""
    try:
        cache.ensure_loaded()
    except Exception:  # noqa: BLE001
        pass
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
