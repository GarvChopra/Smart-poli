import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_safety import scan_red_flags  # noqa: E402


@pytest.mark.parametrize("text", [
    "I can't breathe", "i cannot breathe properly", "mujhe saans nahi aa rahi",
    "saans lene mein bahut dikkat ho rahi hai", "साँस नहीं आ रही", "my father is unconscious",
    "papa behosh ho gaye", "he is having a seizure", "use daura pad raha hai", "chest pain and sweating a lot",
    "seene mein dard aur pasina aa raha hai", "her face is drooping and speech is slurred",
    "bahut zyada khoon beh raha hai", "heavy bleeding that won't stop", "I want to kill myself",
    "mujhe khudkushi karni hai", "मैं आत्महत्या करना चाहता हूँ", "I took too many pills", "maine zyada goliyan kha li",
])
def test_worrying_phrases_are_caught(text):
    flags = scan_red_flags(text)
    assert flags["immediate"] or flags["verify"], text


@pytest.mark.parametrize("text", [
    "I have a mild headache", "mere sir mein dard hai", "no chest pain, just a cough", "I took my medicine",
    "maine dawai le li", "breathing is fine now", "",
])
def test_ordinary_text_does_not_trigger(text):
    assert scan_red_flags(text) == {"immediate": [], "verify": []}, text


def test_returns_human_readable_reasons():
    flags = scan_red_flags("saans nahi aa rahi aur behosh ho raha hoon")
    assert "Loss of consciousness" in flags["immediate"]
    assert flags["verify"][0]["reason"] == "Breathing difficulty"
