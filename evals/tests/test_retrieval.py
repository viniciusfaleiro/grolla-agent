"""O corpus e o critério de aceite do eval de recuperação, sem banco nem modelo."""

from evals.retrieval import EPISODES, PROBES, failures


def test_every_probe_points_to_an_episode_of_the_corpus() -> None:
    keys = {key for key, _, _ in EPISODES}

    assert {probe.expected for probe in PROBES} <= keys
    assert any(probe.paraphrase for probe in PROBES)
    assert any(not probe.paraphrase for probe in PROBES)


def test_paraphrase_must_reach_the_top_with_the_hybrid_search() -> None:
    paraphrase = next(probe for probe in PROBES if probe.paraphrase)

    assert failures([(paraphrase, None, 2)]) == []
    assert "fora do top" in failures([(paraphrase, None, 9)])[0]


def test_hybrid_must_not_lose_what_the_textual_search_finds() -> None:
    exact = next(probe for probe in PROBES if not probe.paraphrase)

    assert failures([(exact, 1, 3)]) == []
    assert "piorou" in failures([(exact, 1, None)])[0]
