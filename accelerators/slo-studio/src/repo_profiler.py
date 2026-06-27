from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


ROUTE_PATTERNS = [
    re.compile(r'@(app|router)\.(get|post|put|delete|patch)\(["\\\']([^"\\\']+)["\\\']'),
    re.compile(r'(GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_\-/{}:.]+)'),
    re.compile(r'path:\s*["\\\']?(/[A-Za-z0-9_\-/{}:.]+)'),
]

DEPENDENCY_HINTS = [
    "redis",
    "postgres",
    "mysql",
    "mongodb",
    "kafka",
    "rabbitmq",
    "sqs",
    "dynamodb",
    "elasticsearch",
    "opensearch",
    "payment",
    "catalog",
    "cart",
    "checkout",
]


def profile_repo(repo_path: str | None) -> dict[str, Any]:
    if not repo_path:
        return {"available": False, "reason": "No repo path provided"}

    path = Path(repo_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        return {"available": False, "reason": f"Repo path not found: {path}"}

    entrypoints: set[str] = set()
    dependencies: set[str] = set()
    languages: set[str] = set()
    files_scanned = 0

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}]

        for filename in files:
            lower = filename.lower()
            full = Path(root) / filename

            if lower.endswith(".py"):
                languages.add("python")
            elif lower.endswith((".js", ".jsx", ".ts", ".tsx")):
                languages.add("javascript/typescript")
            elif lower.endswith(".go"):
                languages.add("go")
            elif lower.endswith(".java"):
                languages.add("java")
            elif lower in {"dockerfile", "docker-compose.yml", "package.json", "requirements.txt", "pom.xml", "build.gradle", "go.mod"}:
                pass
            elif not lower.endswith((".yaml", ".yml", ".json", ".md")):
                continue

            try:
                text = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            files_scanned += 1

            for pattern in ROUTE_PATTERNS:
                for match in pattern.findall(text):
                    if isinstance(match, tuple):
                        entrypoints.add(match[-1])
                    else:
                        entrypoints.add(match)

            text_lower = text.lower()
            for dep in DEPENDENCY_HINTS:
                if dep in text_lower:
                    dependencies.add(dep)

            if files_scanned >= 400:
                break

    return {
        "available": True,
        "repo_path": str(path),
        "files_scanned": files_scanned,
        "languages": sorted(languages),
        "entrypoints": sorted(entrypoints)[:80],
        "dependencies": sorted(dependencies),
    }