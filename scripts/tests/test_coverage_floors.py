from typing import Any

from scripts.coverage_floors import check, totals_for

FLOORS = {"grolla.domain": {"line": 90, "branch": 80}}


def report(statements: int, covered: int, branches: int, covered_branches: int) -> dict[str, Any]:
    summary = {
        "num_statements": statements,
        "covered_lines": covered,
        "num_branches": branches,
        "covered_branches": covered_branches,
    }
    return {
        "files": {
            "packages/grolla-core/src/grolla/domain/incident.py": {"summary": summary},
            "packages/grolla-core/src/grolla/adapters/x.py": {
                "summary": {**summary, "covered_lines": 0}
            },
        }
    }


def test_passes_when_layer_meets_its_floors() -> None:
    assert check(report(100, 95, 10, 9), FLOORS) == []


def test_fails_naming_layer_and_floor_when_line_coverage_is_low() -> None:
    assert check(report(100, 85, 10, 9), FLOORS) == ["grolla.domain: linha 85.0% < piso 90%"]


def test_fails_when_branch_coverage_is_low() -> None:
    assert check(report(100, 95, 10, 7), FLOORS) == ["grolla.domain: branch 70.0% < piso 80%"]


def test_layer_without_code_passes() -> None:
    assert check({"files": {}}, FLOORS) == []


def test_only_counts_files_of_the_layer() -> None:
    assert totals_for(report(100, 95, 10, 9), "grolla.domain").covered_lines == 95
