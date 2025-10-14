# Contributing to mcp-merval

Thank you for considering a contribution to **mcp-merval**. The goal of the project is to provide a lightweight MCP server that works out of the box with Claude Desktop and Cursor, so please keep changes focused on that workflow.

## Code of Conduct

- Treat all contributors with respect.
- Keep discussions technical and constructive.

## Getting Started

```bash
git clone https://github.com/your-username/mcp-merval.git
cd mcp-merval
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
cp broker_config.example.json broker_config.json
```

## Reporting Issues

When filing a bug report, include:

- Python version and operating system.
- Broker used and whether auto-login is configured.
- Steps to reproduce the problem.
- Relevant logs (remove anything sensitive first).

## Pull Requests

1. Fork the repository and branch from `main`.
2. Keep the diff focused—avoid reintroducing HTTP wrappers, Docker files, or deployment scripts.
3. Update documentation when behaviour changes.
4. Add small, targeted tests where practical (e.g., helpers that do not hit live brokers). Automated tests are optional but manual verification steps are appreciated.
5. Rebase before opening the PR and explain the motivation clearly.

Suggested branch prefixes:

- `feat/` for new MCP tools.
- `fix/` for bug fixes.
- `docs/` for documentation updates.
- `broker/` for broker-specific adjustments.

## Style Guidelines

- Use `black`/`ruff` or equivalent to keep formatting tidy (not enforced, but consistency helps).
- Write user-facing strings (logs, errors) in Rioplatense Spanish.
- Keep functions typed and documented with short docstrings.
- Avoid introducing heavy dependencies—`requirements.txt` should stay minimal.

## Project Structure

```
config.py
lib/
  market_helpers.py
  pyrofex_session.py
  session_registry.py
pyRofex-master/
server.py
```

## Security

- Do not commit real credentials. `broker_config.json` is gitignored; leave only the `.example` file updated.
- Report sensitive issues privately before filing a public bug.

By contributing you agree that your work will be released under the MIT License.
