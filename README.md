# codex-refs

Search academic references using [OpenAI Codex CLI](https://github.com/openai/codex) as the search driver, with a bundled deterministic PubMed tool — PMIDs are never fabricated.

This is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill designed to build a verified reference CSV (`refs.csv`) for academic manuscripts.

## What changed in v2

v1 had Codex write PubMed E-utilities code from scratch on every run, which caused persistent issues (PowerShell syntax failures, Unicode mangling, rate limit flailing, and — worst of all — PMID hallucination). v2 bundles a tested `pubmed_search.py` script so Codex only handles search strategy, not plumbing.

| | v1 | v2 |
|---|---|---|
| PubMed access | Codex writes code each time | Bundled `pubmed_search.py` |
| PMID safety | Codex could fabricate PMIDs | All PMIDs come from live esearch/efetch |
| Modes | Single mode | Mode A (targeted) / Mode B (agentic exploration) |
| Rate limiting | None | API key support + exponential backoff |
| Convergence | Codex could loop 20+ times | "3–4 rounds max" rule |
| CEC table | Inline | Separated to [cec-sheet](https://github.com/matsuikentaro1/codex-refs) skill |

## Features

- **Mode A** (targeted): Claude directly calls `pubmed_search.py` for known/classic references — fastest, minimal tokens
- **Mode B** (agentic exploration): Codex CLI searches freely for exploratory questions, using `pubmed_search.py` as its only PubMed tool
- Bibliographic data fetched live from PubMed XML API — zero hallucination
- DOI/PMID deduplication with safe append to existing CSVs
- NCBI API key support (optional, ~9 req/s vs 3 req/s without)
- Exponential backoff for 429/5xx errors
- `--peek` mode for browsing candidates before committing
- `--selftest` for connection/key diagnostics

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [OpenAI Codex CLI](https://github.com/openai/codex) (`npm install -g @openai/codex`)
- Python 3.8+

## Installation

### As a Claude Code skill (recommended)

Copy the skill directory to your Claude Code skills folder:

```bash
git clone https://github.com/matsuikentaro1/codex-refs.git
cp -r codex-refs ~/.claude/skills/codex-refs
```

Then use it in Claude Code by saying "文献検索" or "/codex-refs".

### NCBI API key (optional, recommended)

An API key increases the rate limit from 3 to ~9 requests/second. Get one free at [NCBI](https://www.ncbi.nlm.nih.gov/account/settings/).

```bash
# Option 1: environment variable
export NCBI_API_KEY=your_key_here

# Option 2: file (gitignored)
mkdir -p ~/.claude/skills/codex-refs/.secrets
echo "your_key_here" > ~/.claude/skills/codex-refs/.secrets/ncbi_api_key.txt
```

## Standalone usage

The `pubmed_search.py` script works independently of Claude Code:

```bash
# Browse candidates (no CSV write)
python scripts/pubmed_search.py --query "insomnia cognitive behavioral therapy" --peek

# Commit selected PMIDs to CSV
python scripts/pubmed_search.py --keep 33069326,30353868 \
    --note "33069326=CBT-I meta-analysis; 30353868=long-term outcomes" \
    --out refs.csv

# Connection/key self-test
python scripts/pubmed_search.py --selftest
```

Exit codes: `0` = success / `3` = 0 hits / `4` = connection/key error / `1` = other

## CSV schema

| Column | Description |
|--------|-------------|
| PubMed_ID | PMID (always from live API) |
| Author | Semicolon-separated author list |
| Year | Publication year |
| Title | Article title |
| Journal | ISO abbreviation |
| Volume, Issue, Pages | Standard bibliographic fields |
| doi | DOI |
| abstract | Full abstract text |
| whats_interesting1–5 | Selection rationale (why this paper was chosen) |

## Related tools

- [endnote-insert](https://github.com/matsuikentaro1/endnote-insert) — Convert citation markers in .docx to EndNote field codes
- [PubMed2EndNote](https://github.com/matsuikentaro1/pubmed2endnote) — Chrome extension for interactive citation insertion

## License

MIT
