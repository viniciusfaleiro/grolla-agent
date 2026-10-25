"""Gate de vocabulário: nomes de fornecedor não podem aparecer no núcleo (domain e application).

Lê `[tool.grolla.vocabulary]` do pyproject.toml. O texto é quebrado em palavras por `_`, por
camelCase e por qualquer caractere não alfanumérico, para não acusar falso positivo dentro de
outras palavras ("dado" não contém o token "ado").
"""

import re
import sys
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    token: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: fornecedor '{self.token}' no núcleo"


def tokens(text: str) -> Iterator[str]:
    for chunk in NON_ALNUM.split(text):
        for word in CAMEL_BOUNDARY.split(chunk):
            if word:
                yield word.lower()


def find_violations(files: Iterable[Path], forbidden: set[str]) -> list[Violation]:
    violations: list[Violation] = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            violations.extend(
                Violation(path, number, token) for token in tokens(line) if token in forbidden
            )
    return violations


def main() -> int:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    vocabulary = config["tool"]["grolla"]["vocabulary"]
    files = [file for path in vocabulary["paths"] for file in sorted((ROOT / path).rglob("*.py"))]
    violations = find_violations(files, set(vocabulary["forbidden"]))
    for violation in violations:
        print(violation)
    if violations:
        print(f"vocabulário: {len(violations)} violação(ões)")
        return 1
    print(f"vocabulário: ok ({len(files)} arquivos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
