"""
Observability Studio — Root Entry Point
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Delegates to the platform CLI layer which dispatches to the appropriate
accelerator (ObservaScore, ObsCrawl, …).

Usage:
    python main.py observascore assess --config config/config.yaml
    python main.py observascore check  --config config/config.yaml
    python main.py observascore export --config config/config.yaml

The accelerator CLIs are also callable directly via their installed entry-points:
    observascore assess --config config/config.yaml
"""

from studio-platform.cli.main_cli import main

if __name__ == "__main__":
    main()
