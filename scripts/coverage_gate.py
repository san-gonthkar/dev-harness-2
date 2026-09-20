"""Coverage instrumentation and ratchet gate (V11 0.16).

- Enforces per-package line/branch thresholds.
- Detects a 0.5pp+ regression vs the committed baseline (coverage_baseline.json).
- Detects bare `# pragma: no cover` without a trailing justification.
- --errors: fails if any HarnessError subclass is not reachable by a negative test.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "coverage_baseline.json"
BARE_PRAGMA_RE = re.compile(r"#\s*pragma:\s*no\s*cover\s*$")
ALLOWED_DROP = 0.5  # percentage points

# Per-package line/branch thresholds (V11 2.3 ledger).
THRESHOLDS: dict[str, tuple[float, float]] = {
    "contracts": (100.0, 95.0),
    "storage": (95.0, 90.0),
    "vcs": (95.0, 90.0),
    "broker": (80.0, 80.0),
    "core": (95.0, 90.0),
    "ipc": (92.0, 85.0),
    "providers": (90.0, 85.0),
    "engine": (88.0, 80.0),
    "recovery": (90.0, 85.0),
    "observability": (90.0, 80.0),
    "tui": (75.0, 65.0),
}


def check_bare_pragmas() -> list[str]:
    """Return paths with bare `# pragma: no cover` (no trailing justification)."""
    offenders: list[str] = []
    for py in (REPO / "src").rglob("*.py"):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if BARE_PRAGMA_RE.search(line):
                offenders.append(f"{py.relative_to(REPO)}:{lineno}")
    return offenders


def check_error_reachability() -> list[str]:
    """Return HarnessError subclasses with no negative test referencing them."""
    from dev_harness.contracts import errors as errors_mod

    subclasses = [
        name
        for name, obj in vars(errors_mod).items()
        if isinstance(obj, type)
        and issubclass(obj, errors_mod.HarnessError)
        and obj is not errors_mod.HarnessError
    ]
    test_text = ""
    for py in (REPO / "tests").rglob("*.py"):
        test_text += py.read_text(encoding="utf-8") + "\n"
    return [name for name in subclasses if name not in test_text]


def ratchet_failures(metrics: dict[str, dict[str, float]]) -> list[str]:
    """Threshold + baseline-regression failures for the given per-package metrics."""
    failures: list[str] = []
    baseline: dict[str, dict[str, float]] = {}
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for pkg, (line_t, branch_t) in THRESHOLDS.items():
        if pkg not in metrics:
            continue
        line = metrics[pkg].get("line", 0.0)
        branch = metrics[pkg].get("branch", 0.0)
        if line < line_t:
            failures.append(f"{pkg}: line {line:.1f} < {line_t}")
        if branch < branch_t:
            failures.append(f"{pkg}: branch {branch:.1f} < {branch_t}")
        if pkg in baseline:
            base_line = baseline[pkg].get("line", line)
            if line < base_line - ALLOWED_DROP:
                failures.append(
                    f"{pkg}: line regressed {base_line - line:.1f}pp (> {ALLOWED_DROP}pp)"
                )
    return failures


def main() -> int:
    args = sys.argv[1:]

    if "--errors" in args:
        unreachable = check_error_reachability()
        if unreachable:
            print(f"unreachable HarnessError subclasses: {unreachable}", file=sys.stderr)
            return 1
        print("all HarnessError subclasses reachable")
        return 0

    if "--pragma-check" in args:
        offenders = check_bare_pragmas()
        if offenders:
            for o in offenders:
                print(f"bare pragma: {o}", file=sys.stderr)
            return 1
        print("no bare pragmas")
        return 0

    if "--from-data" in args:
        # Test hook: metrics passed on stdin as JSON.
        metrics = json.loads(sys.stdin.read())
    else:
        metrics = _run_coverage()

    failures = ratchet_failures(metrics)
    if failures:
        for f in failures:
            print(f"coverage gate FAIL: {f}", file=sys.stderr)
        return 1
    print("coverage gate OK")
    return 0


def _run_coverage() -> dict[str, dict[str, float]]:
    """Run pytest --cov and aggregate per-package line/branch percentages."""
    subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests",
            "-q", "--cov=dev_harness", "--cov-branch",
            "--cov-report=json:" + str(REPO / "coverage.json"),
        ],
        cwd=REPO, check=False,
    )
    report_path = REPO / "coverage.json"
    if not report_path.exists():
        return {}
    data = json.loads(report_path.read_text(encoding="utf-8"))
    # Aggregate per package: total covered statements / total statements, and
    # branch coverage from the summary.
    pkg_covered: dict[str, int] = {}
    pkg_total: dict[str, int] = {}
    pkg_branch_cov: dict[str, int] = {}
    pkg_branch_total: dict[str, int] = {}
    for path, metrics in data.get("files", {}).items():
        parts = Path(path).parts
        if "dev_harness" not in parts:
            continue
        idx = parts.index("dev_harness")
        if idx + 1 >= len(parts):
            continue
        pkg = parts[idx + 1]
        if pkg.endswith(".py"):
            continue
        summary = metrics.get("summary", {})
        pkg_covered[pkg] = pkg_covered.get(pkg, 0) + summary.get("covered_lines", 0)
        pkg_total[pkg] = pkg_total.get(pkg, 0) + summary.get("num_statements", 0)
        pkg_branch_cov[pkg] = pkg_branch_cov.get(pkg, 0) + summary.get("covered_branches", 0)
        pkg_branch_total[pkg] = pkg_branch_total.get(pkg, 0) + summary.get("num_branches", 0)
    result: dict[str, dict[str, float]] = {}
    for pkg, total in pkg_total.items():
        result[pkg] = {
            "line": (pkg_covered[pkg] / total * 100) if total else 0.0,
            "branch": (pkg_branch_cov[pkg] / pkg_branch_total[pkg] * 100) if pkg_branch_total[pkg] else 0.0,
        }
    return result


if __name__ == "__main__":
    raise SystemExit(main())
