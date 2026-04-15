import subprocess
from pathlib import Path

def run_assessment(config_path: Path, output_dir: Path, enable_ai: bool) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        "-m",
        "observascore.cli",
        "assess",
        "--config",
        str(config_path),
        "--output",
        str(output_dir),
    ]

    if enable_ai:
        cmd.append("--ai")
    else:
        cmd.append("--no-ai")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    html_file = output_dir / "observascore-report.html"
    json_file = output_dir / "observascore-report.json"

    return {
        "html": str(html_file),
        "json": str(json_file),
    }