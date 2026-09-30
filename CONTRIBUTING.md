# Contributing to sem

First off, thank you for considering contributing to `sem` (the `sempropos` package)! People like you make this tool better for everyone.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Enhancements](#suggesting-enhancements)
  - [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)

## Code of Conduct

By participating in this project, you are expected to uphold general open-source standards of respect and collaboration. Please be nice to each other.

## How Can I Contribute?

### Reporting Bugs

- Ensure the bug was not already reported by searching on GitHub under [Issues](https://github.com/ADPer0705/sempropos/issues).
- If you're unable to find an open issue addressing the problem, open a new one. Be sure to include a title and clear description, as well as the expected vs actual behavior.

### Suggesting Enhancements

- First check [ROADMAP.md](ROADMAP.md) for known gaps and planned work — it is
  the fastest way to find something concrete to contribute.
- Open a new issue with a clear title and description.
- Explain why this enhancement would be useful to most users.
- Keep in mind the core constraint of this project: **fully local, privacy-first, offline-first**.

### Pull Requests

1. Fork the repo and create your branch from `main`.
2. If you've added code that should be tested, add tests.
3. Update the documentation if your changes require it.
4. Ensure the package still imports cleanly (the full test suite is being rebuilt):

   ```bash
   python -c "import sempropos.cli, sempropos.index, sempropos.retrieval, sempropos.sources"
   ```

5. Keep provider integration tests deterministic: mock/offline by default.
6. Open a Pull Request!

## Development Setup

1. Clone the repository: `git clone https://github.com/ADPer0705/sempropos.git`
2. Create a virtual environment: `uv venv`
3. Activate the environment: `source .venv/bin/activate` 
4. Install with development dependencies: `uv sync`
5. To test retrieval locally, run `sem install` (or `sem update` if you already have an index) to bootstrap the index.

### Test Policy

- The full test suite is being rebuilt; CI currently performs an import smoke check.
- Default test mode is mocked/offline for provider integrations.
- Live provider checks (if any) should be opt-in and never required for CI.
- Standard test runs must not require any API key.

