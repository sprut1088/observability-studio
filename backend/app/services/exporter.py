import subprocess
from pathlib import Path

def run_export(config_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        "-m",
        "observascore.cli",
        "export",
        "--config",
        str(config_path),
        "--output",
        str(output_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    files = list(output_dir.glob("*.xlsx"))
    if not files:
        raise RuntimeError("Excel file was not generated")

    return max(files, key=lambda p: p.stat().st_mtime)