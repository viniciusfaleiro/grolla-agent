"""Gate de cobertura por camada: aplica `[tool.grolla.coverage-floors]` sobre o coverage.json.

O coverage.py só tem piso global; aqui cada prefixo de módulo (ex.: `grolla.domain`) tem piso
próprio de linha e de branch. Camada sem código executável passa.
"""

import json
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Totals:
    statements: int = 0
    covered_lines: int = 0
    branches: int = 0
    covered_branches: int = 0

    @staticmethod
    def percent(covered: int, total: int) -> float:
        return 100.0 if total == 0 else 100.0 * covered / total

    @property
    def line_percent(self) -> float:
        return self.percent(self.covered_lines, self.statements)

    @property
    def branch_percent(self) -> float:
        return self.percent(self.covered_branches, self.branches)


def module_dir(module: str) -> str:
    return "/src/" + module.replace(".", "/") + "/"


def totals_for(report: dict[str, Any], module: str) -> Totals:
    totals = Totals()
    marker = module_dir(module)
    for path, data in report["files"].items():
        if marker in "/" + str(path).replace("\\", "/"):
            summary = data["summary"]
            totals.statements += summary["num_statements"]
            totals.covered_lines += summary["covered_lines"]
            totals.branches += summary.get("num_branches", 0)
            totals.covered_branches += summary.get("covered_branches", 0)
    return totals


def check(report: dict[str, Any], floors: Mapping[str, Mapping[str, float]]) -> list[str]:
    failures: list[str] = []
    for module, floor in floors.items():
        totals = totals_for(report, module)
        if totals.line_percent < floor["line"]:
            failures.append(f"{module}: linha {totals.line_percent:.1f}% < piso {floor['line']}%")
        if totals.branch_percent < floor["branch"]:
            failures.append(
                f"{module}: branch {totals.branch_percent:.1f}% < piso {floor['branch']}%"
            )
    return failures


def main() -> int:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floors: Mapping[str, Mapping[str, float]] = config["tool"]["grolla"]["coverage-floors"]
    report: dict[str, Any] = json.loads((ROOT / "coverage.json").read_text(encoding="utf-8"))
    failures = check(report, floors)
    for failure in failures:
        print(failure)
    if failures:
        return 1
    for module in floors:
        totals = totals_for(report, module)
        print(f"{module}: linha {totals.line_percent:.1f}% · branch {totals.branch_percent:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
