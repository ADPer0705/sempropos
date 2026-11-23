"""Unit tests for direct man(7)/mdoc(7) roff parsing and rendered fallback."""

from __future__ import annotations

import gzip
from pathlib import Path

from sempropos.sources.roff_parser import (
    parse_flag_header,
    parse_rendered_man_text,
    parse_roff_source,
    read_man_file,
)

_GNU_DU_ROFF = r""".TH DU "1" "October 2025" "GNU coreutils" "User Commands"
.SH NAME
du \- estimate file space usage
.SH SYNOPSIS
.B du
[\fI\,OPTION\/\fR]... [\fI\,FILE\/\fR]...
.SH DESCRIPTION
Summarize device usage of the set of FILEs, recursively for directories.
.TP
\fB\-a\fR, \fB\-\-all\fR
write counts for all files, not just directories
.TP
\fB\-d\fR, \fB\-\-max\-depth\fR=\fI\,N\/\fR
print the total for a directory only if it is N or fewer levels below the command line argument
.TP
\fB\-h\fR, \fB\-\-human\-readable\fR
print sizes in human readable format (e.g., 1K 234M 2G)
.TP
\fB\-s\fR, \fB\-\-summarize\fR
display only a total for each argument
.SH SEE ALSO
df(1), ls(1)
"""

_MDOC_SSH_ROFF = r""".Dd October 1, 2025
.Dt SSH 1
.Os
.Sh NAME
.Nm ssh
.Nd OpenSSH remote login client
.Sh SYNOPSIS
.Nm ssh
.Op Fl 46AaCfGgKkMNnqsTtVvXxYy
.Op Fl p Ar port
.Ar destination
.Sh DESCRIPTION
.Nm
is a program for logging into a remote machine and for executing commands on a remote machine.
.Bl -tag -width Ds
.It Fl i Ar identity_file
Selects a file from which the identity (private key) for public key authentication is read.
.It Fl p Ar port
Port to connect to on the remote host.
.It Fl L Ar bind_address:port:host:hostport
Specifies that connections to the given TCP port are to be forwarded.
.El
.Sh EXAMPLES
Connect to a remote host on port 2222:
.Dl ssh -p 2222 user@example.com
.Sh SEE ALSO
.Xr scp 1 ,
.Xr sftp 1
"""

_FIND_EXAMPLES_ROFF = r""".TH FIND 1
.SH NAME
find \- search for files in a directory hierarchy
.SH EXPRESSIONS
.TP
.BI \-size " n[cwbkMG]"
File uses less than, more than or exactly n units of space.
.TP
.BI \-mtime " n"
File's data was last modified less than, more than or exactly n*24 hours ago.
.SH EXAMPLES
Simple `find|xargs` approach
.PP
Note that this will work incorrectly if there are any filenames containing newlines, single or double quotes, or spaces.
.PP
Find files named core in or below /tmp and delete them:
.EX
find /tmp -name core -type f -print0 | xargs -0 /bin/rm -f
.EE
The -name test comes before the -type test in order to avoid stat(2) calls.
"""


def test_parse_flag_header_variants() -> None:
    f1 = parse_flag_header("-d, --max-depth=N", "Limit depth")
    assert f1 is not None
    assert f1.short == "-d"
    assert f1.long == "--max-depth"
    assert f1.takes_value is True
    assert f1.value_hint == "N"

    f2 = parse_flag_header("--color[=WHEN]", "Colorize output")
    assert f2 is not None
    assert f2.long == "--color"
    assert f2.takes_value is True
    assert f2.value_hint == "WHEN"

    f3 = parse_flag_header("-size n[cwbkMG]", "Match file size")
    assert f3 is not None
    assert f3.short == "-size"
    assert f3.takes_value is True

    assert parse_flag_header("- list files in archive") is None
    assert parse_flag_header("--") is None


def test_man7_gnu_coreutils_flags_in_description() -> None:
    doc = parse_roff_source(_GNU_DU_ROFF, "du")
    assert doc.dialect == "man"
    assert doc.summary == "estimate file space usage"
    assert "Summarize device usage" in doc.description
    assert doc.synopsis == ["du [OPTION]... [FILE]..."]

    by_short = {f.short: f for f in doc.flags}
    assert "-a" in by_short and by_short["-a"].long == "--all"
    assert "-d" in by_short and by_short["-d"].long == "--max-depth"
    assert by_short["-d"].takes_value is True
    assert by_short["-d"].value_hint == "N"
    assert "-h" in by_short and by_short["-h"].long == "--human-readable"
    assert "-s" in by_short and by_short["-s"].long == "--summarize"
    assert doc.see_also == ["df", "ls"]


def test_mdoc7_openssh_flags_and_examples() -> None:
    doc = parse_roff_source(_MDOC_SSH_ROFF, "ssh")
    assert doc.dialect == "mdoc"
    assert doc.summary == "OpenSSH remote login client"
    by_short = {f.short: f for f in doc.flags}
    assert "-i" in by_short and by_short["-i"].takes_value is True
    assert "-p" in by_short and by_short["-p"].value_hint == "PORT"
    assert "-L" in by_short
    assert len(doc.examples) == 1
    assert doc.examples[0].command == "ssh -p 2222 user@example.com"
    assert "scp" in doc.see_also and "sftp" in doc.see_also


def test_find_examples_filters_out_english_prose() -> None:
    doc = parse_roff_source(_FIND_EXAMPLES_ROFF, "find")
    shorts = {f.short for f in doc.flags}
    assert "-size" in shorts
    assert "-mtime" in shorts

    # Must extract ONLY the real `find ...` command, NOT the surrounding English notes!
    assert len(doc.examples) == 1
    assert doc.examples[0].command == "find /tmp -name core -type f -print0 | xargs -0 /bin/rm -f"
    assert "core" in doc.examples[0].description


def test_read_man_file_gzip_and_so_redirect(tmp_path: Path) -> None:
    man1 = tmp_path / "man1"
    man1.mkdir()
    real_page = man1 / "podman.1.gz"
    real_page.write_bytes(gzip.compress(_GNU_DU_ROFF.replace("du", "podman").encode("utf-8")))

    redirect_page = man1 / "docker.1.gz"
    redirect_page.write_bytes(gzip.compress(b".so man1/podman.1\n"))

    loaded = read_man_file(redirect_page)
    assert loaded is not None
    assert "podman" in loaded


def test_rendered_man_fallback_strips_hyphens_and_reads_description_flags() -> None:
    rendered = """DU(1) User Commands DU(1)

NAME
     du - estimate file space usage

SYNOPSIS
     du [OPTION]... [FILE]...

DESCRIPTION
     Summarize device usage.

     -A, --apparent-size
            print apparent sizes although the appar\u2010
            ent size may differ

     -h, --human-readable
            print sizes in human readable format
"""
    doc = parse_rendered_man_text(rendered, "du")
    assert doc.summary == "estimate file space usage"
    assert len(doc.flags) == 2
    by_short = {f.short: f for f in doc.flags}
    assert "apparent size" in by_short["-A"].description
    assert "appar\u2010" not in by_short["-A"].description


def test_real_world_osc8_docbook_and_jq_roff_idioms() -> None:
    # 1. GNU coreutils 9.11 OSC-8 hyperlink escapes `\X'tty: link ...'`
    osc8_du = r""".TH DU "1" "August 2026" "GNU coreutils 9.11"
.SH NAME
du \- estimate file space usage
.SH DESCRIPTION
.TP
\X'tty: link https://example.org#du-d'\fB\-d, \-\-max\-depth=\,N\fP\X'tty: link'\/\fR
print the total for a directory only if it is N or fewer levels below
"""
    du_doc = parse_roff_source(osc8_du, "du")
    assert len(du_doc.flags) == 1
    assert du_doc.flags[0].short == "-d"
    assert du_doc.flags[0].long == "--max-depth"
    assert du_doc.flags[0].value_hint == "N"

    # 2. DocBook `.PP` -> header -> `.RS 4` -> body -> `.RE` (git.1.gz flags + subcommands)
    docbook_git = r""".TH "GIT" "1"
.SH "NAME"
git \- the stupid content tracker
.SH "OPTIONS"
.PP
\-v, \-\-version
.RS 4
Prints the Git suite version\&.
.RE
.SH "HIGH\-LEVEL COMMANDS (PORCELAIN)"
.PP
\fBgit-add\fR(1)
.RS 4
Add file contents to the index\&.
.RE
"""
    git_doc = parse_roff_source(docbook_git, "git")
    assert any(f.short == "-v" and f.long == "--version" for f in git_doc.flags)
    assert any(s.name == "add" and "index" in s.summary for s in git_doc.subcommands)

    # 3. ronn/pandoc `jq.1.gz` (`INVOKING JQ`, `/` separator, trailing `:`, bare `.IP`)
    jq_roff = r""".TH "JQ" "1"
.SH "NAME"
jq \- Command-line JSON processor
.SH "INVOKING JQ"
.TP
\fB\-\-null\-input\fR / \fB\-n\fR:
.IP
Don\'t read any input at all\.
"""
    jq_doc = parse_roff_source(jq_roff, "jq")
    assert len(jq_doc.flags) == 1
    assert jq_doc.flags[0].short == "-n"
    assert jq_doc.flags[0].long == "--null-input"
    assert "input" in jq_doc.flags[0].description
