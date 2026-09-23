"""Mutation harness gate (V11 0.17).

Runs mutmut against the scoped mutation focus set and reports the score.
Usage:
    python scripts/mutation_gate.py --packages storage,vcs,broker,core
    python scripts/mutation_gate.py --dry-run
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Mutation focus set (V11 0.17 / 1.C / 4.C / 6.C / 8.C).
SCOPED_MODULES = [
    "storage",
    "vcs",
    "broker",
    "core",
    "engine.dag",
    "engine.worker_pool",
    "engine.worker_workspace",
    "engine.integrator",
]

# Per-package minimum mutation scores.
MIN_SCORES = {
    "storage": 80.0,
    "vcs": 80.0,
    "broker": 80.0,
    "core": 85.0,
    "engine.dag": 80.0,
    "engine.worker_pool": 80.0,
    "engine.worker_workspace": 80.0,
    "engine.integrator": 80.0,
}


def run_mutmut(packages: list[str], dry_run: bool = False) -> dict[str, dict]:
    """Run mutmut for the given modules and return per-module results."""
    results: dict[str, dict] = {}
    for module in packages:
        target = f"dev_harness.{module}"
        cmd = [
            sys.executable,
            "-m",
            "mutmut",
            "run",
            "--paths-to-mutate",
            target,
        ]
        if dry_run:
            cmd.append("--quiet")
        proc = subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True, check=False
        )
        # Parse the "Mutation score" line from mutmut output.
        score = _parse_score(proc.stdout + proc.stderr)
        results[module] = {
            "score": score,
            "exit": proc.returncode,
            "dry_run": dry_run,
        }
    return results


def _parse_score(output: str) -> float | None:
    for line in output.splitlines():
        if "mutation score" in line.lower():
            for token in line.replace("%", " ").split():
                try:
                    val = float(token)
                    if 0 <= val <= 100:
                        return val
                except ValueError:
                    continue
    return None


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    packages = SCOPED_MODULES
    if "--packages" in args:
        idx = args.index("--packages")
        packages = [p.strip() for p in args[idx + 1].split(",")]

    results = run_mutmut(packages, dry_run=dry_run)
    report = REPO / "reports" / "mutation_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if dry_run:
        print(f"dry-run complete; report at {report}")
        return 0

    failures: list[str] = []
    for module, res in results.items():
        score = res["score"]
        if score is None:
            failures.append(f"{module}: no score parsed")
            continue
        if score < MIN_SCORES.get(module, 0.0):
            failures.append(f"{module}: score {score:.1f} < {MIN_SCORES[module]}")
    if failures:
        for f in failures:
            print(f"mutation gate FAIL: {f}", file=sys.stderr)
        return 1
    print("mutation gate OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
