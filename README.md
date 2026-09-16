# site-context-mcp

## Problem

Agents invent project and site claims without citations. When you ask what a public site says about a product page, a topic note, or an essay, you often get fluent prose and no way to verify it against the live corpus.

## Solution

A **stdio MCP server** that:

1. Fetches public pages starting from `llms.txt`, then follows same-host links to core pages
2. Caches them in-process (~1-hour TTL)
3. Exposes tools that return structured answers with a top-level `sources[]` of public URLs on every response

No secrets, no LinkedIn scraping, no private admin / WOD-APP links — only HTTPS to the wired public site host.

This package is for **site/project context**. It does **not** duplicate resume identity or resume-summary tools (see sibling [resume-mcp](https://github.com/YongBoYu1/resume-mcp) for that).

The default corpus is [YongBo Yu’s public site](https://yongbo-yu.vercel.app). The same fetch/cache/source-URL shape can be pointed at any public `llms.txt`-indexed site corpus.

## Requirements

- Python 3.11+
- Network access to the wired public host (default: `yongbo-yu.vercel.app`) when running the live server

## Install

```bash
# from this repo
pip install -e .

# or with uv
uv pip install -e .
```

Dev / smoke tests (mocked HTTP; no live network required):

```bash
pip install -e ".[dev]"
pytest -q
```

## Run (stdio)

```bash
# module entrypoint
python -m site_context_mcp

# console script (after install)
site-context-mcp

# with uv
uv run python -m site_context_mcp
```

The process speaks MCP over **stdin/stdout**. Do not pipe unrelated stdout into the same process.

## Cursor `mcp.json`

Add a server entry (Cursor: Settings → MCP, or edit `~/.cursor/mcp.json` / project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "site-context-mcp": {
      "command": "python3",
      "args": ["-m", "site_context_mcp"],
      "cwd": "/absolute/path/to/site-context-mcp"
    }
  }
}
```

If the package is installed into a venv, point `command` at that interpreter (or use the `site-context-mcp` console script).

With `uv`:

```json
{
  "mcpServers": {
    "site-context-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "site_context_mcp"],
      "cwd": "/absolute/path/to/site-context-mcp"
    }
  }
}
```

After saving, reload MCP servers in Cursor. The tools below should appear as `site-context-mcp` tools.

## Tools

| Tool | Purpose |
|------|---------|
| `list_site_pages` | Catalog of cached public pages from `llms.txt` + linked core pages |
| `get_page` | One page by path (`/projects/kilodock`) or absolute HTTPS URL |
| `search_site` | Keyword search over the cached corpus (snippets + source URLs) |
| `get_project` | Project evidence page (default: `kilodock`), with optional light `resume.json` cross-link |

Every tool response is JSON text that includes a top-level `sources` array of public URLs. Partial fetch failures are reported in `cache_notes` when present.

## Example prompts (in Cursor / Claude)

- “List the public pages this MCP has cached and cite the source URLs.”
- “Get `/projects/kilodock` and quote the engineering metrics with sources.”
- “Search the site corpus for ‘LangGraph’ / ‘Codex’ and return snippets with URLs.”
- “Use `get_project` for KiloDock evidence — do not invent stack claims.”

## Data sources (public only)

On startup (and on first tool use if needed), the server fetches:

1. `https://yongbo-yu.vercel.app/llms.txt`
2. Same-host pages linked from `llms.txt` (home, about, project pages, topic pages, essays)
3. Optionally `resume.json` when linked — used only for light project cross-links, not as a resume clone

Binaries and off-host / private targets are skipped. No API keys.

## Author

**YongBo Yu** (also **Yong Yu**) — Toronto, Canada · GitHub [YongBoYu1](https://github.com/YongBoYu1)

- Site: [https://yongbo-yu.vercel.app](https://yongbo-yu.vercel.app)
- KiloDock evidence: [https://yongbo-yu.vercel.app/projects/kilodock](https://yongbo-yu.vercel.app/projects/kilodock)
- Site index: [https://yongbo-yu.vercel.app/llms.txt](https://yongbo-yu.vercel.app/llms.txt)

## License

MIT — see [LICENSE](LICENSE).
