"""Multi-source documentation extractors and deterministic ToolSpec merger."""

from sempropos.sources.completions import extract_completion_fragments
from sempropos.sources.discovery import (
    DiscoveryContext,
    compute_missing_binaries,
    discover_path_binaries,
)
from sempropos.sources.help_probe import is_safe_to_probe, parse_help_output, probe_tool_help
from sempropos.sources.man import discover_man_files, extract_man_bundles
from sempropos.sources.merger import (
    ManBundle,
    ToolSourceBundle,
    attach_symlink_aliases,
    merge_tool_bundle,
)
from sempropos.sources.roff_parser import (
    ParsedManDocument,
    parse_flag_header,
    parse_rendered_man_text,
    parse_roff_source,
    read_man_file,
)
from sempropos.sources.tldr import (
    extract_tldr_fragments,
    find_local_tldr_sources,
    parse_tldr_markdown,
    sync_tldr_archive,
)

__all__ = [
    "DiscoveryContext",
    "discover_path_binaries",
    "compute_missing_binaries",
    "ParsedManDocument",
    "parse_flag_header",
    "parse_roff_source",
    "parse_rendered_man_text",
    "read_man_file",
    "discover_man_files",
    "extract_man_bundles",
    "extract_tldr_fragments",
    "find_local_tldr_sources",
    "parse_tldr_markdown",
    "sync_tldr_archive",
    "extract_completion_fragments",
    "is_safe_to_probe",
    "parse_help_output",
    "probe_tool_help",
    "ManBundle",
    "ToolSourceBundle",
    "attach_symlink_aliases",
    "merge_tool_bundle",
]
