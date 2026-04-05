# AGENTS.md — sempropos

> **sempropos** is a smart, local, offline CLI tool that takes a natural language task description
> and returns the exact shell command or pipeline to accomplish it — by doing semantic retrieval
> over installed man pages and synthesizing a command with a tiny local LLM.

---

## Project Goal

Build a Python CLI tool `sempropos` that:
1. At install time: indexes all man pages on the system into a structured SQLite database + embedding vectors
2. At query time: retrieves the most relevant tools + flags for the user's query, then uses a local LLM to synthesize the exact command

**Key constraint**: fully local and offline after install. No API calls. No cloud. No persistent background process.

---

## Repository Layout

```
sempropos/
├── AGENTS.md                  ← this file
├── README.md
├── pyproject.toml             ← packaging metadata
├── install.sh                 ← bootstraps llama-cli binary + model GGUF download
├── sempropos/
│   ├── __init__.py
│   ├── cli.py                 ← entry point, argument parsing
│   ├── index/
│   │   ├── __init__.py
│   │   ├── builder.py         ← install-time indexing pipeline
│   │   ├── parser.py          ← man page parser (SYNOPSIS, OPTIONS, EXAMPLES)
│   │   ├── schema.py          ← SQLite schema definition + migrations
│   │   └── staleness.py       ← package DB mtime-based update detection
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── bm25.py            ← BM25 search over tool descriptions
│   │   ├── semantic.py        ← embedding-based search (MiniLM)
│   │   ├── fusion.py          ← Reciprocal Rank Fusion of BM25 + semantic results
│   │   ├── flag_retrieval.py  ← flag-level semantic search per candidate tool
│   │   └── expansion.py       ← synonym-based query expansion
│   ├── synthesis/
│   │   ├── __init__.py
│   │   ├── prompt.py          ← prompt construction from retrieved context
│   │   └── backend.py         ← llama.cpp / Ollama inference, Tier 0 fallback
│   └── config.py              ← paths, constants, backend detection
└── tests/
    ├── test_parser.py
    ├── test_retrieval.py
    └── test_synthesis.py
```

---

## Data Directory Layout

```
~/.local/share/sempropos/
  index.db                     ← SQLite database (schema below)
  tool_embeddings.npy          ← float32 array, shape [N_tools, 384]
  flag_embeddings.npy          ← float32 array, shape [N_flags, 384]
  flag_embedding_ids.npy       ← int32 array mapping flag_embeddings rows → flags.id
  last_indexed                 ← plaintext ISO timestamp
  models/
    qwen2.5-1.5b-q4_k_m.gguf
  bin/
    llama-cli                  ← platform-specific binary
```

---

## SQLite Schema

File: `sempropos/index/schema.py`

```sql
CREATE TABLE IF NOT EXISTS tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    section     INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL,     -- one-liner from man -k
    synopsis    TEXT               -- raw SYNOPSIS block text
);

CREATE TABLE IF NOT EXISTS flags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    flag         TEXT NOT NULL,    -- e.g. "-l", "--list"
    long_flag    TEXT,             -- long form if separate from short
    takes_value  BOOLEAN DEFAULT 0,
    value_hint   TEXT,             -- e.g. "file", "count", "format"
    description  TEXT NOT NULL     -- flag's one-liner from OPTIONS section
);

CREATE TABLE IF NOT EXISTS examples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    command      TEXT NOT NULL,    -- literal example command string
    context      TEXT              -- surrounding prose if any
);

CREATE INDEX IF NOT EXISTS idx_flags_tool ON flags(tool_id);
CREATE INDEX IF NOT EXISTS idx_examples_tool ON examples(tool_id);
```

---

## Module Specifications

### `sempropos/cli.py`

Entry point. Uses `argparse`.

**Commands:**
- `sempropos "<query>"` — main query flow
- `sempropos --install` — run full index build + download model/binary
- `sempropos --update` — re-run indexing (same as install-time index, skip binary/model download if present)
- `sempropos --check` — print staleness status (last indexed, package DB mtime, whether update is needed)

**Staleness check on every query**: before retrieval, call `staleness.is_stale()`. If stale, print a one-line warning:
```
[sempropos] Package database has changed. Run `sempropos --update` to refresh index.
```
Do not block the query.

**Backend detection on every query**: call `backend.detect()` which returns one of `["llama_cpp", "ollama", "tier0"]`. Tier 0 prints structured output without synthesis.

---

### `sempropos/index/parser.py`

Man page parser. Input: raw text from `man -P cat <tool>`. Output: structured dict.

```python
def parse_man_page(raw_text: str) -> dict:
    """
    Returns:
    {
        "synopsis": str,          # raw SYNOPSIS section text
        "flags": [                # parsed from OPTIONS / FLAGS section
            {
                "flag": str,
                "long_flag": str | None,
                "takes_value": bool,
                "value_hint": str | None,
                "description": str
            }
        ],
        "examples": [             # parsed from EXAMPLES section
            {
                "command": str,
                "context": str | None
            }
        ]
    }
    """
```

**Section detection**: split on all-caps lines that match `r'^\s*[A-Z][A-Z\s]+\s*$'`. Track current section as a state variable. Accumulate lines per section.

**Flag parsing** (in OPTIONS section): primary regex:
```python
FLAG_PATTERN = re.compile(
    r'^\s{0,8}'
    r'(-[\w-]+)'                    # short or long flag
    r'(?:,\s*(-[\w-]+))?'           # optional second form
    r'(?:\s+[<=\[]?(\w+)[>=\]]?)?'  # optional value hint
    r'\s{2,}(.+)'                    # description (2+ spaces gap)
)
```

If this regex fails on a line, fall back to storing the full OPTIONS block as a single flag entry with `flag="*"` and `description=<full block>`. This handles non-standard man pages gracefully.

**Example detection** (in EXAMPLES section): a line is an example command if it matches `r'^\s+([$#>]?\s*\w[\w.-]*)' ` and is not pure prose. Capture the command line; associate any preceding prose line as `context`.

**Edge cases to handle:**
- Man pages with no OPTIONS section: return empty flags list, do not crash
- Man pages with no EXAMPLES section: return empty examples list
- Groff escape sequences (`.B`, `.I`, `\fB`, `\fR` etc): strip before parsing with `re.sub(r'\\f[BIRP]', '', text)` and `re.sub(r'\.[A-Z]+\s', '', text)`
- Pages that are just "see also" stubs redirecting to another page: detect with `re.search(r'see\s+\w+\s*\(\d\)', text, re.IGNORECASE)`, follow redirect with `man -P cat <redirected_tool>`

---

### `sempropos/index/builder.py`

Install-time indexing pipeline. Should be resumable: if interrupted, re-running continues from where it left off (check `SELECT COUNT(*) FROM tools` before starting, skip already-indexed tools).

```python
def build_index(progress: bool = True) -> None:
    """
    1. Run `man -k . -s 1` via subprocess, parse output into list of (name, section, description)
    2. For each tool:
       a. Run `man -P cat <name>` via subprocess
       b. Call parser.parse_man_page()
       c. INSERT into tools, flags, examples tables
       d. Embed tools.description with MiniLM → accumulate into list
       e. Embed each flag.description → accumulate into list
    3. Write tool_embeddings.npy (N_tools × 384)
    4. Write flag_embeddings.npy (N_flags × 384)
    5. Write flag_embedding_ids.npy (N_flags,) mapping row → flags.id
    6. Write last_indexed timestamp
    """
```

**Embedding**: use `sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")`. Batch embed in chunks of 256 for memory efficiency. The model auto-downloads on first `--install` run (~80MB, one-time).

**Progress display**: use `tqdm` if `progress=True`. Show current tool name. On failure for a specific tool (man page unreadable, parser crash), log warning and continue — never abort the full index for one bad page.

**Concurrency**: use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` for the `man -P cat` subprocess calls. DB writes must be serialized (single writer thread). Embedding batching is already vectorized.

---

### `sempropos/index/staleness.py`

```python
PACKAGE_DB_PATHS = {
    "debian": "/var/lib/dpkg/status",
    "arch":   "/var/lib/pacman/sync",
    "fedora": "/var/lib/rpm/Packages",
    "alpine": "/lib/apk/db/installed",
}

def detect_package_db() -> str | None:
    """Return path to package DB for current distro, or None if unknown."""

def is_stale() -> bool:
    """
    Return True if any detected package DB mtime is newer than last_indexed timestamp.
    If last_indexed does not exist, always return True.
    """
```

---

### `sempropos/retrieval/expansion.py`

Static synonym table. No model. Return expanded token list from the query.

```python
SYNONYMS: dict[str, list[str]] = {
    "see":       ["list", "view", "show", "display", "print", "read"],
    "files":     ["contents", "entries", "members", "paths"],
    "archive":   ["compress", "extract", "zip", "tar", "gz", "7z", "bz2", "xz"],
    "delete":    ["remove", "rm", "erase", "unlink", "clean"],
    "find":      ["search", "locate", "grep", "scan", "filter"],
    "network":   ["socket", "tcp", "udp", "interface", "packet", "port", "http"],
    "kill":      ["terminate", "stop", "signal", "process", "pid"],
    "disk":      ["partition", "mount", "filesystem", "df", "du", "block"],
    "user":      ["account", "passwd", "group", "permission", "sudo"],
    "monitor":   ["watch", "top", "stat", "trace", "profile", "log"],
    "convert":   ["transform", "encode", "decode", "transcode", "format"],
    "download":  ["fetch", "get", "pull", "curl", "wget", "request"],
    "send":      ["transfer", "push", "upload", "scp", "rsync", "copy"],
    "text":      ["string", "line", "grep", "sed", "awk", "parse"],
    "image":     ["photo", "picture", "png", "jpg", "jpeg", "svg", "resize"],
    "run":       ["execute", "launch", "start", "spawn", "exec"],
    "schedule":  ["cron", "timer", "at", "systemd", "interval"],
    "encrypt":   ["decrypt", "cipher", "gpg", "ssl", "tls", "hash", "sign"],
}

def expand(query: str) -> list[str]:
    """Tokenize query, add synonyms for recognized terms, return deduplicated list."""
```

Add more domains as needed. This table is intentionally hand-curated — do not generate it programmatically.

---

### `sempropos/retrieval/bm25.py`

```python
def search(query_tokens: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    """
    Load or build BM25 index from tools.description corpus.
    Returns list of (tool_id, score) sorted descending.
    BM25 index is built from SQLite on first call and cached in-process.
    Uses rank_bm25.BM25Okapi.
    """
```

Tokenize descriptions by splitting on whitespace + punctuation, lowercase. Do not use NLTK or any NLP library — keep it stdlib + rank_bm25 only.

---

### `sempropos/retrieval/semantic.py`

```python
def search(query: str, top_k: int = 10) -> list[tuple[int, float]]:
    """
    Embed query with MiniLM.
    Load tool_embeddings.npy.
    Compute cosine similarity (numpy dot product on L2-normalized vectors).
    Return list of (tool_id, score) sorted descending.
    """
```

Embeddings are pre-normalized at index time. Cosine similarity at query time is just a dot product — no sklearn needed.

---

### `sempropos/retrieval/fusion.py`

Reciprocal Rank Fusion of BM25 and semantic result lists.

```python
def reciprocal_rank_fusion(
    results: list[list[tuple[int, float]]],
    k: int = 60
) -> list[tuple[int, float]]:
    """
    Standard RRF: score(d) = sum over lists of 1 / (k + rank(d))
    Input: list of ranked result lists, each [(tool_id, score), ...]
    Output: unified sorted list of (tool_id, fused_score)
    """
```

---

### `sempropos/retrieval/flag_retrieval.py`

```python
def get_relevant_flags(
    tool_id: int,
    query: str,
    top_k: int = 6
) -> list[dict]:
    """
    1. Load flag embeddings for this tool_id (subset of flag_embeddings.npy)
    2. Embed query with MiniLM
    3. Cosine similarity → top_k flag rows
    4. Fetch full flag records from SQLite
    5. Return list of flag dicts: {flag, long_flag, takes_value, value_hint, description}
    
    If tool has fewer than top_k flags, return all of them.
    If tool has no flags, return empty list.
    """
```

---

### `sempropos/synthesis/prompt.py`

```python
def build_prompt(
    query: str,
    candidates: list[dict]   # [{tool, synopsis, flags, examples}, ...]
) -> str:
    """
    Build the synthesis prompt. candidates is already filtered to top 1-3 tools
    with their top-k flags and all examples.
    
    Prompt structure:
      System instruction (one line)
      Task line
      For each candidate tool:
        --- <tool>(1) ---
        Synopsis: <synopsis>
        Relevant flags:
          <flag>  <takes_value hint if any>  — <description>
          ...
        Examples:
          <command>
          ...
        ---------
      
      Command:
    
    Total prompt target: under 600 tokens.
    If synopsis or examples are missing for a tool, omit that section silently.
    If multiple tools are plausible, include all — let the model pick.
    """
```

**System instruction**: `"You are a CLI command synthesizer. Output ONLY the exact shell command or pipeline. No explanation. No markdown. No preamble."`

---

### `sempropos/synthesis/backend.py`

```python
def detect() -> str:
    """
    Returns "llama_cpp" | "ollama" | "tier0"
    
    Check order:
    1. ~/.local/share/sempropos/bin/llama-cli exists and is executable → "llama_cpp"
    2. `which llama-cli` in PATH → "llama_cpp"
    3. socket connect to 127.0.0.1:11434 succeeds → "ollama"
    4. else → "tier0"
    """

def run(prompt: str, backend: str) -> str:
    """
    "llama_cpp": subprocess call to llama-cli with args:
        -m <model_path>
        --prompt <prompt>
        -n 80               # max output tokens
        --temp 0.1          # near-deterministic
        -c 1024             # context window (prompt is <600 tokens)
        --no-display-prompt
        --log-disable
    
    "ollama": POST to http://localhost:11434/api/generate
        model: "qwen2.5:1.5b" (check available models first, prefer 1.5b, fall back to 0.5b)
        prompt: <prompt>
        stream: false
    
    "tier0": return empty string (caller handles structured output fallback)
    """
```

**Tier 0 fallback output** (in `cli.py`, when `backend == "tier0"`):
```
No local LLM detected. Showing matched tools:

[7z] — A file archiver with high compression ratio
  Synopsis: 7z <command> [<switches>] <archive_name> [<files>]
  Key flags: l (list contents), e (extract), a (add to archive)
  Examples:
    7z l archive.7z
    7z l -ba archive.7z

Install llama.cpp or start Ollama for command synthesis.
```

---

## Dependencies

```toml
[project]
dependencies = [
    "rank-bm25>=0.2.2",
    "sentence-transformers>=3.0.0",
    "numpy>=1.26",
    "tqdm>=4.0",
    "requests>=2.31",       # for Ollama API backend only
]
```

Do not add LangChain, LlamaIndex, or any agent framework. Do not add chromadb, faiss, or any vector DB — numpy dot products over flat arrays are sufficient at this scale.

---

## Install Script (`install.sh`)

The install script must:
1. Detect OS and CPU arch (`uname -s`, `uname -m`)
2. Download the appropriate `llama-cli` prebuilt binary from `github.com/ggerganov/llama.cpp/releases` → `~/.local/share/sempropos/bin/llama-cli`
3. Download `qwen2.5-1.5b-instruct-q4_k_m.gguf` from HuggingFace (Qwen2.5-1.5B-Instruct-GGUF repo) → `~/.local/share/sempropos/models/`
4. `pip install --user sempropos` (or `pipx install sempropos`)
5. Run `sempropos --install` (the indexing step)
6. Print instructions to add `~/.local/bin` to PATH if not already present

The script must be idempotent — running it twice does not re-download or re-index if artifacts already exist.

---

## Testing Requirements

- `test_parser.py`: test SYNOPSIS/OPTIONS/EXAMPLES extraction against 5 real man page fixtures (store as `.txt` files in `tests/fixtures/`). Include: `tar`, `7z`, `curl`, `grep`, `ffmpeg`. These cover common formatting variations.
- `test_retrieval.py`: test BM25, semantic search, and RRF with a minimal 20-tool mock index. Assert top result for known queries.
- `test_synthesis.py`: test prompt construction. Assert that prompt for a known query+candidates is under 600 tokens (use `len(prompt.split())` as a proxy). Do not test LLM output — that is non-deterministic.

---

## Non-Goals (do not implement)

- GUI or TUI
- Cloud sync or telemetry
- Continuous background process or daemon
- Support for man page sections other than 1 (user commands) and 8 (sysadmin commands)
- Windows or macOS support in v1 (Linux only)
- LLM fine-tuning or training

---

## Definition of Done

- [ ] `sempropos --install` completes without error on a fresh Debian/Ubuntu install
- [ ] Index build completes in under 5 minutes on a Core i5 equivalent
- [ ] `sempropos "list files in archive.7z"` returns `7z l archive.7z` (or equivalent correct command)
- [ ] `sempropos "find all files larger than 100MB"` returns a correct `find` invocation
- [ ] `sempropos "monitor network traffic on eth0"` returns a correct `tcpdump` or `iftop` invocation
- [ ] Tier 0 fallback produces readable structured output when no LLM is detected
- [ ] Staleness warning appears after a simulated package DB mtime update
- [ ] All tests pass
