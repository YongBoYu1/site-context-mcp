"""Fetch and cache a public website corpus for site/project context."""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

SITE_HOST = "yongbo-yu.vercel.app"
SITE_URL = f"https://{SITE_HOST}"
LLMS_TXT_URL = f"{SITE_URL}/llms.txt"
RESUME_JSON_URL = f"{SITE_URL}/resume.json"
KILODOCK_URL = f"{SITE_URL}/projects/kilodock"
GITHUB_URL = "https://github.com/YongBoYu1"

# Re-fetch at most once per this many seconds within a process.
CACHE_TTL_SECONDS = 3600
USER_AGENT = "site-context-mcp/0.1.0 (+https://github.com/YongBoYu1/site-context-mcp)"

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BINARY_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".webm",
    ".mp3",
)
_BLOCKED_HOST_FRAGMENTS = (
    "linkedin.com",
    "admin.kilodock.com",
    "wod-app",
)


@dataclass
class SitePage:
    """One cached public page (HTML, plain text, or JSON)."""

    url: str
    path: str
    title: str
    description: str | None
    text: str
    content_type: str
    kind: str  # "html" | "text" | "json" | "llms"


@dataclass
class PublicSiteCache:
    """In-memory cache of llms.txt plus linked same-host pages."""

    llms_text: str | None = None
    pages: dict[str, SitePage] = field(default_factory=dict)
    resume: dict[str, Any] | None = None
    fetched_at: float | None = None
    errors: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ensure_loaded(self, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            fresh = (
                self.fetched_at is not None
                and (now - self.fetched_at) < CACHE_TTL_SECONDS
                and self.llms_text is not None
                and bool(self.pages)
            )
            if fresh and not force:
                return
            self._fetch_unlocked()

    def _fetch_unlocked(self) -> None:
        errors: list[str] = []
        pages: dict[str, SitePage] = {}
        llms_text: str | None = None
        resume: dict[str, Any] | None = None

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html, text/plain, application/json, */*",
        }
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            try:
                resp = client.get(LLMS_TXT_URL)
                resp.raise_for_status()
                llms_text = resp.text
                pages[normalize_url(LLMS_TXT_URL)] = SitePage(
                    url=LLMS_TXT_URL,
                    path="/llms.txt",
                    title="llms.txt",
                    description="Machine-readable site index",
                    text=llms_text,
                    content_type="text/plain",
                    kind="llms",
                )
            except Exception as exc:  # noqa: BLE001 — surface as tool error context
                errors.append(f"Failed to fetch {LLMS_TXT_URL}: {exc}")

            seed_urls = discover_urls(llms_text or "")
            # Always include home + known project evidence even if parsing fails.
            for fallback in (SITE_URL + "/", KILODOCK_URL, RESUME_JSON_URL):
                if fallback not in seed_urls:
                    seed_urls.append(fallback)

            for url in seed_urls:
                if not is_allowed_public_url(url):
                    continue
                norm = normalize_url(url)
                if norm in pages:
                    continue
                try:
                    page = fetch_page(client, url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Failed to fetch {url}: {exc}")
                    continue
                if page is None:
                    continue
                pages[normalize_url(page.url)] = page
                if page.kind == "json" and page.path.rstrip("/") == "/resume.json":
                    try:
                        parsed = json.loads(page.text)
                        if isinstance(parsed, dict):
                            resume = parsed
                    except json.JSONDecodeError as exc:
                        errors.append(f"resume.json is not valid JSON: {exc}")

        self.llms_text = llms_text
        self.pages = pages
        self.resume = resume
        self.errors = errors
        self.fetched_at = time.monotonic()

    def require_corpus(self) -> dict[str, SitePage]:
        self.ensure_loaded()
        if not self.pages:
            detail = "; ".join(self.errors) or "unknown error"
            raise RuntimeError(f"Public site corpus is unavailable: {detail}")
        return self.pages

    def get_page_by_ref(self, ref: str) -> SitePage | None:
        """Resolve a path, slug, or absolute URL to a cached page."""
        pages = self.require_corpus()
        ref = (ref or "").strip()
        if not ref:
            return None

        if ref.startswith("http://") or ref.startswith("https://"):
            if not is_allowed_public_url(ref):
                return None
            return pages.get(normalize_url(ref))

        path = ref if ref.startswith("/") else f"/{ref}"
        target = normalize_url(urljoin(SITE_URL + "/", path.lstrip("/")))
        if target in pages:
            return pages[target]

        # Soft match on path suffix (e.g. "projects/kilodock").
        needle = path.rstrip("/").lower()
        for page in pages.values():
            if page.path.rstrip("/").lower() == needle:
                return page
            if page.path.rstrip("/").lower().endswith(needle):
                return page
        return None


cache = PublicSiteCache()


def normalize_url(url: str) -> str:
    """Normalize URL for cache keys (drop fragment; collapse trailing slash except root)."""
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    # Treat bare host as /
    if not path:
        path = "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def url_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path or "/"


def is_allowed_public_url(url: str) -> bool:
    """HTTPS-only, same public host, no binaries / private / LinkedIn targets."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.netloc or "").lower()
    if host != SITE_HOST:
        return False
    path = (parsed.path or "").lower()
    if any(path.endswith(ext) for ext in _BINARY_EXTENSIONS):
        return False
    lowered = url.lower()
    if any(frag in lowered for frag in _BLOCKED_HOST_FRAGMENTS):
        return False
    return True


def discover_urls(llms_text: str) -> list[str]:
    """Extract markdown links from llms.txt in document order."""
    found: list[str] = []
    seen: set[str] = set()
    for _label, href in _MD_LINK_RE.findall(llms_text or ""):
        href = href.strip()
        if not is_allowed_public_url(href):
            continue
        key = normalize_url(href)
        if key in seen:
            continue
        seen.add(key)
        found.append(href)
    return found


def fetch_page(client: httpx.Client, url: str) -> SitePage | None:
    """Fetch one public URL; skip binary responses."""
    resp = client.get(url)
    resp.raise_for_status()
    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type.startswith("image/") or content_type in {
        "application/pdf",
        "application/zip",
        "application/octet-stream",
        "font/woff",
        "font/woff2",
    }:
        return None

    final_url = str(resp.url)
    if not is_allowed_public_url(final_url):
        # Redirected off-host — do not keep.
        return None

    path = url_path(final_url)
    body = resp.text

    if "json" in content_type or path.endswith(".json"):
        # Pretty-print if possible for searchable text.
        try:
            parsed = json.loads(body)
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            text = body
        title = path.rsplit("/", 1)[-1] or "json"
        return SitePage(
            url=normalize_url(final_url) if not urlparse(final_url).query else final_url,
            path=path,
            title=title,
            description=None,
            text=text,
            content_type=content_type or "application/json",
            kind="json",
        )

    if "html" in content_type or (not content_type and "<html" in body[:500].lower()):
        title, description, text = extract_html(body)
        return SitePage(
            url=normalize_url(final_url),
            path=path,
            title=title or path,
            description=description,
            text=text,
            content_type=content_type or "text/html",
            kind="html",
        )

    # Plain text (llms.txt and similar)
    title = path.rsplit("/", 1)[-1] or path
    return SitePage(
        url=normalize_url(final_url),
        path=path,
        title=title,
        description=None,
        text=body,
        content_type=content_type or "text/plain",
        kind="text",
    )


class _TextExtractor(HTMLParser):
    """Collect visible text from preferred regions; strip scripts/styles/nav noise lightly."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta_description: str | None = None
        self.main_parts: list[str] = []
        self.body_parts: list[str] = []
        self._capture_title = False
        self._skip_depth = 0
        self._in_main = 0
        self._in_body = 0
        self._prefer_tags = {"main", "article"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = True
        if tag == "meta":
            name = attr.get("name", "").lower()
            prop = attr.get("property", "").lower()
            if name == "description" or prop == "og:description":
                content = attr.get("content", "").strip()
                if content and not self.meta_description:
                    self.meta_description = content
        if tag == "body":
            self._in_body += 1
        if tag in self._prefer_tags:
            self._in_main += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "template"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = False
        if tag == "body" and self._in_body:
            self._in_body -= 1
        if tag in self._prefer_tags and self._in_main:
            self._in_main -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._capture_title:
            self.title_parts.append(text)
            return
        if self._in_main:
            self.main_parts.append(text)
        elif self._in_body:
            self.body_parts.append(text)


def extract_html(raw: str) -> tuple[str, str | None, str]:
    """Return (title, description, visible_text) from HTML."""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 — fall back to crude strip
        stripped = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
        stripped = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", stripped)
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        text = html_lib.unescape(re.sub(r"\s+", " ", stripped)).strip()
        return "", None, text

    title = html_lib.unescape(" ".join(parser.title_parts)).strip()
    # Drop site suffix often present as "Title | YongBo Yu"
    if "|" in title:
        title = title.split("|", 1)[0].strip() or title

    parts = parser.main_parts or parser.body_parts
    text = html_lib.unescape(re.sub(r"\s+", " ", " ".join(parts))).strip()
    return title, parser.meta_description, text


def project_slug_from_path(path: str) -> str | None:
    m = re.match(r"^/projects/([^/]+)/?$", path or "")
    return m.group(1) if m else None
