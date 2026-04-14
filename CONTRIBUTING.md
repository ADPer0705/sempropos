# Contributing to sempropos

First off, thank you for considering contributing to `sempropos`! People like you make this tool better for everyone.

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

- Open a new issue with a clear title and description.
- Explain why this enhancement would be useful to most users.
- Keep in mind the core constraint of this project: **fully local, privacy-first, offline-first**.

### Pull Requests

1. Fork the repo and create your branch from `main`.
2. If you've added code that should be tested, add tests.
3. Update the documentation if your changes require it.
4. Ensure the full quality checks pass:

   ```bash
   python -m pytest tests/ --cov=src/sempropos --cov-report=term-missing --cov-report=html --cov-fail-under=65
   ```

5. Keep tests deterministic: provider/API tests should be mocked/offline in CI.
6. Open a Pull Request!

## Development Setup

1. Clone the repository: `git clone https://github.com/ADPer0705/sempropos.git`
2. Create a virtual environment: `python -m venv .venv`
3. Activate the environment: `source .venv/bin/activate` 
4. Install with development dependencies: `python -m pip install -e ".[dev]"`
5. To test retrieval locally, run `sempropos --install` to bootstrap the index.

### Test Policy

- Minimum repository coverage target: **65%**.
- Default test mode is mocked/offline for provider integrations.
- Live provider checks (if any) should be opt-in and never required for CI.
- Standard test runs must not require any API key.

**Note**: The architecture guide in `AGENTS.md` provides detailed context on how the CLI structure is orchestrated.
