---
type: architecture
title: Retrieval and Ranking
description: Stopword filtering, singular forms, weighted directional synonym expansion, multi-column BM25 with artifact and intent boosts, confidence-gated semantic recall, hybrid fusion semantics, and per-tool flag selection.
tags: [retrieval, bm25, embeddings, fusion, ranking]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-72fe826953aaa47d17a811e4
    resource: repo://ROADMAP.md
  - id: openwiki-source-2f4a1d1392b204104c5fce86
    resource: repo://src/sempropos/retrieval/bm25.py
  - id: openwiki-source-330d3b01cba3dd95d77d0949
    resource: repo://src/sempropos/retrieval/expansion.py
  - id: openwiki-source-0032f2d73652a4f2b635b6f5
    resource: repo://src/sempropos/retrieval/flag_retrieval.py
  - id: openwiki-source-f1189b24619594b1a36787c8
    resource: repo://src/sempropos/retrieval/fusion.py
  - id: openwiki-source-7c67f95f32cdcea8bd004f44
    resource: repo://src/sempropos/retrieval/query.py
  - id: openwiki-source-3b165e2454412725ff556c11
    resource: repo://src/sempropos/retrieval/semantic.py
  - id: openwiki-source-ff870d0a5a99aff8f66b5e2b
    resource: repo://src/sempropos/service.py
generated: { by: "antigravity", at: "2026-11-23T21:48:04.805Z" }
---

# Retrieval and Ranking

Retrieval combines a weighted lexical ranker, an optional semantic ranker, and a fusion step that deliberately keeps lexical evidence in control. `ask` retrieves 20 BM25 candidates and up to 20 semantic candidates; `search` retrieves `max(limit * 2, 20)` from each.

## Query analysis

`src/sempropos/retrieval/query.py` turns a sentence into `AnalyzedQuery`:

- `tokenize` lowercases and extracts `[a-z0-9]+` runs.
- `STOPWORDS` removes natural-language filler such as `the`, `how`, `please`, `me`, `command`, and `using`, while deliberately keeping short command names (`ls`, `du`, `df`, `cp`, `mv`, `rm`, `ss`, `ps`, `ip`, `jq`, `7z`, `uv`, `rg`, `fd`, `nc`, `dd`, `tr`, `wc`, `id`, `ln`, `vi`). If filtering would empty the query, the raw tokens are kept.
- Primary tokens are deduplicated in order and receive a naive singular form. A trailing `s` is stripped when the token is longer than three characters and does not end in `ss`, `us`, or `is`; `-ies` becomes `-y`.
- Explicit flag mentions such as `--max-depth` or `-name` are captured in `flag_tokens`.
- File extensions and compound artifacts add tool boosts: `.7z` boosts `7z`, `.tar.gz` boosts `tar`, `.json` boosts `jq`, `.service` boosts `systemctl`, and so on. The first mapped tool gets 1.8 and later tools step down from 1.3 to a floor of 0.7.
- Compound intent rules require every token group to match and then boost canonical tools: disk usage → `du`, free space → `df`, listening ports → `ss`, file age/size predicates → `find`, git operations → `git`, service control → `systemctl`, JSON formatting → `jq`, text replacement → `sed`, and similar rules.

The result exposes `has_strong_hint` when either artifact or intent boosts are present.

## Query expansion

`src/sempropos/retrieval/expansion.py` uses directional, weighted expansions instead of symmetric synonym cliques, which avoids runaway transitive drift. Each term maps to `(expanded_term, weight)` pairs, for example `folder → directory 0.85, dir 0.55`, `disk → filesystem 0.65, space 0.65, storage 0.55`, and `port → socket 0.85, tcp 0.6, udp 0.6, listening 0.6`.

`expand_weighted` keeps the maximum weight per term and excludes terms already present, so an expansion never outranks the user's own vocabulary. `expand_query` returns primary tokens followed by expansions sorted by descending weight. Porter stemming in SQLite handles inflection separately, so the expansion table only needs domain vocabulary.

## BM25 lexical ranking

`src/sempropos/retrieval/bm25.py` ranks tools from `tools_fts`.

### Query construction

`build_fts_query(tokens, operator="OR")`:

- strips every character that is not a word character and trims underscores;
- de-duplicates case-insensitively;
- quotes each token to prevent FTS5 keyword collisions (`AND`, `OR`, `NOT`, `NEAR`);
- joins with `OR` or `AND`; an empty result produces no query.

### Multi-signal scoring

`_score_with_conn` runs up to four lookups with `LIMIT max(top_k * 4, 40)`:

1. an OR query over primary tokens;
2. an AND query over distinct primary tokens when there are 2–5 of them;
3. an OR query over the 12 highest-weight expansion terms;
4. direct name and alias lookups for raw tokens, artifact targets, and intent targets.

`tools_fts` uses column weights `12.0, 10.0, 6.0, 4.0, 4.0, 3.0, 1.5` for name, aliases, summary, tags, subcommands, examples, and flags. Raw BM25 magnitude is squashed monotonically with `magnitude / (magnitude + 8.0)` rather than min-max normalized, so scores are comparable across queries.

Each candidate accumulates:

```text
score = OR_hit
      + (AND_bonus 1.15 + 0.35 * AND_hit) when all primary tokens match
      + 0.35 * synonym_hit
      + 1.6 when a raw token equals the tool name
      + 1.4 when a raw token equals an alias
      + artifact boost
      + intent boost
```

Results sort by descending score, then ascending tool id, and truncate to `top_k`. A `category` filter restricts candidates to exact case-insensitive category matches.

The exact-name and alias boosts are why direct requests such as `find`, `tar`, `ss`, or `jq` reliably reach the named tool even when the sentence is noisy.

## Semantic ranking

`src/sempropos/retrieval/semantic.py` memory-maps `tool_embeddings.npy` with `mmap_mode="r"` and caches the id list plus matrix in module globals. The cache is keyed by the data directory and `index.meta` mtime, so an index update triggers an automatic reload; `reload_embeddings()` clears it explicitly.

Loading behavior:

- missing vector files produce an empty matrix and no semantic results;
- the matrix must be 2-D `float32`;
- the id count must equal the matrix row count;
- the id list must exactly equal `SELECT id FROM tools ORDER BY id`.

Any structural mismatch raises `RuntimeError(config.MISMATCH_UPDATE_NOTICE)`. `query_semantic_matches` returns `[]` when `embedding.provider = "none"`, embeds the query with `embed_texts([query], kind="query")`, verifies the dimension, L2-normalizes both sides, computes cosine similarity, and returns the top-k ids by descending score.

## Confidence gate

`should_run_semantic` in `fusion.py` decides whether a cold query pays the ONNX model load:

- always run when embeddings are already warm or `embedding.lazy_fallback = false`;
- always run when BM25 returned nothing;
- when an artifact boost is present and the top score is at least 1.5, skip semantics;
- otherwise skip only when the top score is at least 1.65 and leads the runner-up by at least 0.45.

`sem search` uses a stricter rule: it calls semantics only when the embedding model is already warm or BM25 returned nothing, keeping the command effectively LLM-free.

## Fusion

`src/sempropos/retrieval/fusion.py` exports two functions.

### `reciprocal_rank_fusion`

Standard RRF: add `1 / (k + rank)` per tool across input lists, sum, sort descending with tool id as tie-break. `k` defaults to 60 and must be positive. The function remains exported but is not called by the query path.

### `hybrid_fusion`

The production path keeps BM25 primary:

1. Build `semantic_rank` from the semantic list; tools absent from it get `len + 1`.
2. Sort BM25 results by descending lexical score, then semantic rank (only to break exact lexical ties), then tool id.
3. Assign rank-based scores `1 / (60 + rank)` to that lexical order.
4. Append semantic-only tools that BM25 missed with score `0.0`, optionally capped by `semantic_recall_limit`.

The returned list is non-increasing, so later sorting cannot reorder lexical results. Semantics contribute recall, not re-ranking. The module explicitly warns against symmetrically blending the two score distributions, because min-max normalization can let a small cosine gap outvote an exact-name match.

## Candidate selection and flag retrieval

For `ask`, the service takes the first five fused tools, falling back to the raw BM25 list and then the semantic list, and loads up to 8 flags, 4 examples, and 4 subcommands per candidate. A `--tool` filter returns that tool alone with up to 12 flags, 6 examples, and 6 subcommands.

For `search`, each of the top `limit` results gets up to 3 flags, 2 examples, and 3 subcommands.

`src/sempropos/retrieval/flag_retrieval.py` scopes FTS to one tool:

- subcommands use `subcommands_fts` with `bm25(subcommands_fts, 4.0, 1.0)`, then fall back to the first subcommands by id;
- flags use `flags_fts` with `bm25(flags_fts, 3.0, 1.0)`, fill remaining slots from matched subcommands, then fall back to top-level flags by id, de-duplicated by canonical short/long key;
- examples use `examples_fts`, prioritize tldr examples, weight descriptions at 2.5 versus commands at 1.0, fill from matched subcommands, and finally from all examples, de-duplicated by command.

Flags and examples are not embedded; they are ranked lexically on demand, which keeps the vector matrix small.

## End-to-end summary

```text
query
  ├─ stopword filter + singulars + flag/artifact/intent hints
  ├─ BM25 OR + AND + directional expansion + direct name lookup
  │    └─ weighted columns, saturation, name/alias/artifact/intent boosts
  ├─ confidence gate
  │    └─ semantic cosine top 20 (when warm, needed, or lazy fallback off)
  └─ hybrid_fusion
       ├─ lexical order preserved (semantic breaks exact ties)
       └─ semantic-only recall tail at score 0
            └─ top 5 tools
                 └─ up to 8 FTS-ranked flags, 4 subcommands, 4 examples each
```

## Related pages

- [Architecture Overview](architecture.md)
- [Index Database Schema](index-schema.md)
- [Prompting and Synthesis Providers](synthesis.md)
- [Indexing Pipeline](indexing.md)
