"""CLI package."""


def main() -> None:
	from .main import main as _main

	_main()


__all__ = ["main"]
