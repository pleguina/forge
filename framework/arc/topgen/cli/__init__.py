"""CLI package."""


def main() -> int:
	from .main import main as _main

	return _main()


__all__ = ["main"]
