"""The shared classifier framework. Design revision 3, F13.

Its second consumer is task 3.3's correction/supersession classifier, which does
not exist yet — so these tests pin the contract that task will inherit, not just
the behaviour the gate happens to need today.
"""

from __future__ import annotations

import inspect

import pytest

from program import config
from program.engine import ollama
from program.integrity import classifier, gate


@pytest.fixture(autouse=True)
def _restore_config():
    """Several tests below repoint ANAM_CONFIG_DIR. monkeypatch restores the
    env var but not the parsed config, which is cached — so a stale value would
    leak into the next test as a phantom failure."""
    yield
    config.reload()


@pytest.fixture
def sent(monkeypatch):
    """Capture what reached the Ollama client."""
    seen = {}

    def fake(messages, **kwargs):
        seen["messages"] = messages
        seen.update(kwargs)
        return "CONSISTENT"

    monkeypatch.setattr(classifier.ollama, "chat_text", fake)
    return seen


# --- the reply grammar -------------------------------------------------------


def test_consistent_parses_as_no_contradiction():
    verdict = classifier.parse("CONSISTENT")

    assert verdict.contradicts is False
    assert verdict.items == ()


def test_contradicts_parses_its_items():
    verdict = classifier.parse(
        "CONTRADICTS\n- since yesterday | not running between turns\n- and again | same"
    )

    assert verdict.contradicts is True
    assert verdict.items == (
        "since yesterday | not running between turns", "and again | same")


def test_contradicts_without_items_is_still_a_contradiction():
    """The verdict is the signal; the itemisation is detail. Discarding an
    unitemised CONTRADICTS would turn a flag into a pass."""
    verdict = classifier.parse("CONTRADICTS")

    assert verdict.contradicts is True and verdict.items == ()


@pytest.mark.parametrize("reply", ["CONSISTENT.", "consistent", "  CONSISTENT  "])
def test_the_verdict_word_is_read_leniently(reply):
    """Punctuation and case are not the contract; the word is."""
    assert classifier.parse(reply).contradicts is False


@pytest.mark.parametrize("reply", ["", "   ", "I'm not sure", "maybe?", "42", None])
def test_an_unusable_reply_raises_rather_than_passing(reply):
    """**Silence is not consent.** A model that answers neither verdict has not
    said the statement is fine, and reading it as clean would make "checked and
    passed" unfalsifiable. The caller turns this into `unavailable`."""
    with pytest.raises(ollama.OllamaResponseError):
        classifier.parse(reply)


def test_the_raw_reply_is_carried_for_a_later_reader():
    assert classifier.parse("CONTRADICTS\nno items here").raw.startswith("CONTRADICTS")


# --- the call ----------------------------------------------------------------


def test_the_call_uses_the_classifier_settings_not_the_chat_defaults(sent, monkeypatch):
    monkeypatch.setenv("ANAM_CHAT_MODEL", "some-chat-model")
    config.reload()

    classifier.classify("a prompt")

    assert sent["model"] == config.classifier_model()
    assert sent["options"]["num_predict"] == config.classifier_num_predict()
    assert sent["timeout"] == config.classifier_timeout_seconds()
    assert sent["messages"] == [{"role": "user", "content": "a prompt"}]


def test_the_classifier_model_inherits_the_chat_model_when_unpinned(monkeypatch):
    monkeypatch.setenv("ANAM_CHAT_MODEL", "inherited-model")
    config.reload()

    assert config.classifier_model() == "inherited-model"


def test_pinning_the_classifier_model_overrides_the_chat_model(monkeypatch, tmp_path):
    """The measured hazard this setting exists for: under muse-glimmer:30b the
    gate returned empty content on 21 of 21 calls, so changing the chat model
    silently disabled the checker."""
    (tmp_path / "defaults.toml").write_text(
        '[models]\nchat = "some-chat-model"\n'
        '[integrity]\nclassifier_model = "pinned-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ANAM_CONFIG_DIR", str(tmp_path))
    config.reload()

    assert config.chat_model() == "some-chat-model"
    assert config.classifier_model() == "pinned-model"


@pytest.mark.parametrize("key, value", [
    ("classifier_num_predict", 0),
    ("classifier_num_predict", -1),
    ("classifier_timeout_seconds", 0),
    ("classifier_timeout_seconds", -5),
])
def test_a_nonsense_setting_raises_rather_than_defaulting(monkeypatch, tmp_path, key, value):
    (tmp_path / "defaults.toml").write_text(
        f"[integrity]\n{key} = {value}\n", encoding="utf-8")
    monkeypatch.setenv("ANAM_CONFIG_DIR", str(tmp_path))
    config.reload()

    with pytest.raises(config.ConfigError):
        getattr(config, key)()


def test_the_settings_are_bootstrap_only_not_live_editable():
    """The in-flight grace floor is derived from the timeout. A live-editable
    value underneath a correctness floor is how that floor quietly stops
    holding, so none of these is in the settings registry."""
    from program.settings import store

    registered = {spec.key for spec in store.SETTINGS}
    for key in ("integrity.classifier_model", "integrity.classifier_num_predict",
                "integrity.classifier_timeout_seconds"):
        assert key not in registered


def test_the_measured_budget_covers_the_measured_worst_verdict():
    """51 output tokens was the worst verdict measured over 30 real calls
    (`python -m scripts.measure_classifier_budget`). The budget is not a chosen
    round number: below what a verdict costs, Ollama returns *empty content*
    rather than a truncated verdict, which reads as unparseable and becomes
    `unavailable` — the failure measured under muse-glimmer:30b."""
    assert config.classifier_num_predict() >= 2 * 51


def test_the_timeout_keeps_the_grace_floor_where_the_derivation_put_it():
    """The floor is 2000 + T seconds rounded up. Recomputed rather than
    asserted, so raising the timeout without re-deriving the floor fails here."""
    import math

    derived = 2000 + config.classifier_timeout_seconds()

    assert math.ceil(derived / 60) == config.IN_FLIGHT_GRACE_FLOOR_MINUTES


# --- what the framework deliberately does not carry --------------------------


def test_the_framework_takes_no_actor_and_no_addressee():
    """Fabrication is not a permissions question, and addressee context was
    *measured* as a non-fix at task 3.6a (45% -> 50% false positives). It is not
    built in for task 3.3 to inherit."""
    params = set(inspect.signature(classifier.classify).parameters)

    assert not params & {"actor", "user_id", "role", "user", "speaker", "addressee"}


def test_the_framework_holds_no_prompt_text():
    """What is shared is the plumbing. The prompt, the ground truth and the
    findings vocabulary belong to each consumer — two prompt variants of one
    mechanism, not one prompt with a flag."""
    source = inspect.getsource(classifier)

    for owned_by_the_gate in ("HOW THE SYSTEM ACTUALLY WORKS", "STATEMENT TO CHECK",
                              "architecture.md", "soul.md"):
        assert owned_by_the_gate not in source


def test_the_gate_reaches_ollama_only_through_the_framework():
    """One place builds the call, so the second consumer cannot drift from the
    first by calling the client directly."""
    source = inspect.getsource(gate)

    assert "ollama.chat" not in source
    assert "classifier.classify" in source
