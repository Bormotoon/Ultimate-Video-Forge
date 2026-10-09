"""Single packaged executable with explicit GUI/CLI/worker dispatch."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--worker":
        from studio.stages.worker import main as worker

        return worker(arguments[1:])
    if arguments and arguments[0] == "--gui":
        from studio.app import gui_main

        return gui_main()
    if arguments and arguments[0] == "--check-resources":
        from studio.app import main as check

        return check(arguments)
    from studio.cli.main import main as cli

    return cli(arguments[1:] if arguments and arguments[0] == "--cli" else arguments)


if __name__ == "__main__":
    raise SystemExit(main())
