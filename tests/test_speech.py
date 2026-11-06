import threading
import time

from connect4 import speech
from connect4.speech import Speaker


def test_disabled_speaker_is_silent(capsys):
    Speaker(False).say("Column 4.")
    assert capsys.readouterr().out == ""


def test_newer_suggestion_replaces_unspoken_one(monkeypatch):
    spoken, release = [], threading.Event()

    def fake_run(cmd, **kwargs):
        spoken.append(cmd[-1])
        release.wait(2)         # hold the first message so others queue up

    monkeypatch.setattr(speech.shutil, "which", lambda name: "/usr/bin/say")
    monkeypatch.setattr(speech.subprocess, "run", fake_run)
    speaker = Speaker(True)
    speaker.say("Red blundered!")
    time.sleep(0.1)
    speaker.say("Column 3.", replaces="suggestion")
    speaker.say("Change: column 5.", replaces="suggestion")
    speaker.say("Red wins.")
    release.set()
    time.sleep(0.3)
    assert spoken == ["Red blundered!", "Change: column 5.", "Red wins."]
