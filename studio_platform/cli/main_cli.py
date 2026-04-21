"""
studio_platform.cli.main_cli
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Platform-controlled CLI entry point for Observability Studio.

This module delegates to each accelerator's own CLI so that the platform
controls the top-level entry while accelerators remain independently runnable.

Usage (from root main.py):
    from studio_platform.cli.main_cli import main
    main()

Direct CLI usage (accelerator CLIs are still callable independently):
    observascore assess --config config/config.yaml
"""

import sys


def main() -> None:
    """Dispatch to the appropriate accelerator CLI based on argv[1]."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_help()
        return

    accelerator = sys.argv[1].lower()

    if accelerator == "observascore":
        # Delegate to the ObservaScore Click CLI; strip the accelerator name
        # from argv so Click sees its own subcommands cleanly.
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from observascore.cli import cli as observascore_cli
        observascore_cli()

    else:
        print(f"Unknown accelerator: {accelerator!r}", file=sys.stderr)
        _print_help()
        sys.exit(1)


def _print_help() -> None:
    print(
        "Observability Studio — Accelerator Platform\n"
        "\n"
        "Usage:\n"
        "  python main.py observascore <command> [options]\n"
        "\n"
        "Available accelerators:\n"
        "  observascore    Observability & SRE maturity assessment\n"
        "\n"
        "Run 'python main.py observascore --help' for accelerator-specific help.\n"
    )
