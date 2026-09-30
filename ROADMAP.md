# Roadmap & open work

This is the living list of known gaps and planned work. It is intentionally
short and concrete: each item should be something a contributor can pick up.
Check items off in the PR that closes them, and move shipped items into the
release notes rather than keeping them here.

Retrieval is deliberately **BM25-primary** (see
`src/sempropos/retrieval/fusion.py` for why). Items below are ordered by the
observed impact on recommendation quality.

## Retrieval & ranking

- [ ] **Stem the FTS5 index.** `tools_fts`/`flags_fts` use the default
      `unicode61` tokenizer, so `ports` never matches the document token
      `sockets`. This is why `ss` cannot be retrieved for a port-scan query.
      Switch to `tokenize='porter unicode61'`. Requires an index rebuild
      (`INDEX_REVISION`).
- [ ] **Index richer tool text.** Only the one-line `man -k` description is
      indexed and embedded; the stored `synopsis` is unused for retrieval.
      Embed `name + description + synopsis` (and maybe the top flag
      descriptions) so the semantic signal has something to work with. Requires
      a rebuild (`EMBEDDING_REVISION`).
- [ ] **Recover intent verbs.** Bag-of-words OR ranking lets a word like
      `delete` dominate the tool choice: `"delete files older than 30 days"`
      surfaces `mdel`/`userdel`/`systemd-tmpfiles` instead of `find`. Consider
      intent/verb weighting or a small curated verb→capability hint map.
- [ ] **Surface `du` for disk-usage queries.** `"show disk usage by folder"`
      does not reach `du`, even though it is indexed.
- [ ] **Reconsider `reciprocal_rank_fusion`.** It is exported but no longer
      called. Either wire it in somewhere it earns its place or remove it.

## Indexing & discovery

- [ ] **Tools without a `man -k` entry are invisible.** `7z` is installed and
      runnable but absent from the index (no whatis/man entry), so
      `"list files in an archive.7z"` suggests `unzip`. Consider a fallback
      source (`--help` parsing, package-file heuristics) or a curated map for
      well-known tools.
- [ ] **Make the discovery gap observable.** `sem check` could report installed
      binaries that are missing from the index instead of silently omitting
      them.

## Tests

- [ ] **Rebuild the test suite.** CI currently only does an import smoke check.
- [ ] **Add retrieval regression tests.** The behavior verified for 0.3.0
      should be pinned so it cannot silently regress, e.g.:
      - `"find markdown files"` / `"find markdowns"` ranks `find` first.
      - A semantic-only hit can never outrank a lexical hit.
      - The fused order does not depend on how deep the input lists are fetched.

## Documentation

- [ ] Document the retrieval pipeline (BM25-primary + semantic recall tail) for
      contributors, including the "do not make it a symmetric score blend" note
      now living in `fusion.py`.
