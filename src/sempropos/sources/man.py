"""man(1) page source (sections 1 and 8)."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator

from sempropos.sources.base import ToolRecord

MAN_K_PATTERN = re.compile(r"^([^\s,]+)(?:,\s*[^\s,]+)*\s+\(([^)]+)\)\s+-\s+(.*)$")


class ManSource:
    """Discover and read system man pages for command-line tools."""

    name = "man"
    sections = (1, 8)

    def _list_section(self, section: int) -> list[ToolRecord]:
        try:
            result = subprocess.run(
                ["man", "-k", ".", "-s", str(section)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError("Failed to execute man -k") from exc

        if result.returncode != 0:
            return []

        rows: list[ToolRecord] = []
        for line in result.stdout.splitlines():
            match = MAN_K_PATTERN.match(line.strip())
            if not match:
                continue

            name, section_raw, description = match.groups()
            section_match = re.search(r"\d+", section_raw)
            if not section_match:
                continue

            section_num = int(section_match.group(0))
            if section_num not in self.sections:
                continue

            rows.append(
                ToolRecord(
                    name=name,
                    section=section_num,
                    description=description.strip(),
                    source=self.name,
                )
            )
        return rows

    def discover(self) -> Iterator[ToolRecord]:
        combined: list[ToolRecord] = []
        for section in self.sections:
            try:
                combined.extend(self._list_section(section))
            except RuntimeError:
                # A missing section is not fatal (e.g. no section 8 pages).
                continue

        seen: set[tuple[str, int]] = set()
        for record in combined:
            key = (record.name, record.section)
            if key in seen:
                continue
            # Only index tools that are actually installed and runnable.
            if not shutil.which(record.name):
                continue
            seen.add(key)
            yield record

    def load(self, record: ToolRecord) -> str | None:
        result = subprocess.run(
            ["man", "-P", "cat", str(record.section), record.name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout
