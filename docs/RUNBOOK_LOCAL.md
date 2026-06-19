# Local Runbook

## Environment

This workspace currently uses the checked local Windows virtual environment from WSL:

```bash
.venv/Scripts/python.exe --version
```

On a fresh machine, create a Python 3.13 environment and install dependencies:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

In this WSL checkout, use `.venv/Scripts/python.exe -m ...` when bare `python` is not
on PATH.

## Verification

Fast local loop:

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pyright
```

Focused routing test:

```bash
.venv/Scripts/python.exe -m pytest tests/test_order_ticket_routing_v1.py
```

## Safety Checks

Before calling a branch complete:

- Confirm `git status --short --branch`.
- Confirm the branch has no credentials, broker tokens, or `.env` files.
- Confirm docs link to canonical law instead of duplicating it.
- Confirm safety-sensitive changes have rejection-first tests.

## GitHub Branch Protection

After Phase 1 CI is merged and green on `main`, enable branch protection or an
equivalent repository ruleset requiring the CI workflow before merge.

If GitHub returns this API error, it is an account/repository-plan blocker rather
than a local-code blocker:

```text
Upgrade to GitHub Pro or make this repository public to enable this feature.
```
