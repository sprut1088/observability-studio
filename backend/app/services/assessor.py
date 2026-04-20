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

    # The report generator writes a fixed name; use glob as a safe fallback in
    # case a future version uses a timestamped filename.
    html_files = sorted(
        output_dir.glob("*.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    json_files = sorted(
        output_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not html_files:
        raise RuntimeError(
            "Assessment CLI exited successfully but produced no HTML report. "
            f"stdout: {result.stdout[:300]}  stderr: {result.stderr[:300]}"
        )

    return {
        "html": str(html_files[0]),
        "json": str(json_files[0]) if json_files else "",
    }
