"""``python -m ops`` entry point for the human control plane."""

from .commands import main

if __name__ == "__main__":
    raise SystemExit(main())
