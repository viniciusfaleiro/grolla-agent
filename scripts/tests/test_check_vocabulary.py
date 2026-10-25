from pathlib import Path

import pytest

from scripts.check_vocabulary import find_violations, tokens

FORBIDDEN = {"spike", "ado", "teams", "signoz"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("spike_incident_id", ["spike", "incident", "id"]),
        ("SigNozClient", ["sig", "noz", "client"]),
        ("AdoClient", ["ado", "client"]),
        ("HTTPServer", ["http", "server"]),
        ("o dado chegou", ["o", "dado", "chegou"]),
    ],
)
def test_tokenizes_by_underscore_camel_case_and_punctuation(text: str, expected: list[str]) -> None:
    assert list(tokens(text)) == expected


def test_flags_vendor_field_with_file_and_line(tmp_path: Path) -> None:
    source = tmp_path / "incident.py"
    source.write_text("class Incident:\n    spike_incident_id: str\n", encoding="utf-8")

    violations = find_violations([source], FORBIDDEN)

    assert [(v.line, v.token) for v in violations] == [(2, "spike")]
    assert str(violations[0]) == f"{source}:2: fornecedor 'spike' no núcleo"


def test_ignores_vendor_name_inside_other_words(tmp_path: Path) -> None:
    source = tmp_path / "dado.py"
    source.write_text("# o dado do lado foi validado\nsteams = 1\n", encoding="utf-8")

    assert find_violations([source], FORBIDDEN) == []
