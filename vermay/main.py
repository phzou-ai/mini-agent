from __future__ import annotations

import sys

from .cli.prompt import run_prompt
from .cli.subcommands import run_subcommand


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {"serve", "memory", "skills", "eval", "mcp"}:
        run_subcommand(sys.argv[1:])
        return

    run_prompt(sys.argv[1:])


if __name__ == "__main__":
    main()
