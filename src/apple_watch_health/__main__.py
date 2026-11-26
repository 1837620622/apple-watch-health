"""Allow invoking via `python -m apple_watch_health`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
