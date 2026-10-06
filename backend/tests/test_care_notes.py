"""Care notes: the rules (length, kinds, medicine ownership, rate limit, visibility). No HTTP here."""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import care_notes as cn  # noqa: E402
from db import CareNote, SessionLocal  # noqa: E402
from main import app  # noqa: E402
from timing_helpers import add_medicine, new_patient  # noqa: E402


@pytest.fixture()
def world():
    with TestClient(app) as client:
        pid, user = new_patient(client)
        other_pid, _ = new_patient(client, "Someone Else")
        med, _ = add_medicine(pid, "Telma", [datetime(2030, 1, 1, 8)])
        other_med, _ = add_medicine(other_pid, "Iron", [datetime(2030, 1, 1, 8)])
    db = SessionLocal()
    yield SimpleNamespace(db=db, pid=pid, med=med, other_med=other_med,
                          caregiver=SimpleNamespace(id=user["id"], role="caregiver", name="Sita"))
    db.close()


def test_a_message_is_stored_trimmed_and_visible_to_the_patient(world):
    n = cn.create_note(world.db, world.pid, world.caregiver, "message", "  Doctor visit tomorrow  ")
    assert n.body == "Doctor visit tomorrow" and n.visibility == "patient" and n.medicine_id is None
    assert [x["body"] for x in cn.list_notes(world.db, world.pid, "patient")] == ["Doctor visit tomorrow"]
    s = cn.serialize_note(n)
    assert s["author_name"] == "Sita" and s["author_role"] == "caregiver" and s["seen"] is False


def test_handover_notes_are_care_team_only(world):
    cn.create_note(world.db, world.pid, world.caregiver, "handover", "Gave her tea at 5, BP felt high")
    assert cn.list_notes(world.db, world.pid, "patient") == []
    assert len(cn.list_notes(world.db, world.pid, "caregiver")) == 1
    assert len(cn.list_notes(world.db, world.pid, "doctor")) == 1


def test_a_medicine_note_must_name_one_of_this_patients_medicines(world):
    ok = cn.create_note(world.db, world.pid, world.caregiver, "medicine", "Take with milk", medicine_id=world.med)
    assert ok.medicine_id == world.med
    with pytest.raises(cn.NoteError):
        cn.create_note(world.db, world.pid, world.caregiver, "medicine", "x note", medicine_id=world.other_med)   # someone else's
    with pytest.raises(cn.NoteError):
        cn.create_note(world.db, world.pid, world.caregiver, "medicine", "x note")                               # none named
    only = cn.list_notes(world.db, world.pid, "caregiver", medicine_id=world.med)
    assert [x["body"] for x in only] == ["Take with milk"]


@pytest.mark.parametrize("body", ["", "   ", "a" * 501, None])
def test_empty_and_overlong_notes_are_refused(world, body):
    with pytest.raises(cn.NoteError):
        cn.create_note(world.db, world.pid, world.caregiver, "message", body)


def test_exactly_500_characters_is_allowed_and_unknown_kinds_are_refused(world):
    assert len(cn.create_note(world.db, world.pid, world.caregiver, "message", "a" * 500).body) == 500
    with pytest.raises(cn.NoteError):
        cn.create_note(world.db, world.pid, world.caregiver, "shout", "hello")


def test_thirty_notes_an_hour_is_the_limit(world):
    for i in range(cn.RATE_LIMIT):
        cn.create_note(world.db, world.pid, world.caregiver, "message", f"note {i}")
    with pytest.raises(cn.NoteRateLimited):
        cn.create_note(world.db, world.pid, world.caregiver, "message", "one too many")
    old = world.db.query(CareNote).filter(CareNote.author_user_id == world.caregiver.id).first()
    old.created_at = datetime.utcnow() - timedelta(hours=2)                   # an older one no longer counts
    world.db.commit()
    cn.create_note(world.db, world.pid, world.caregiver, "message", "fits again")


def test_unread_seen_and_soft_delete(world):
    a = cn.create_note(world.db, world.pid, world.caregiver, "message", "first")
    cn.create_note(world.db, world.pid, world.caregiver, "message", "second")
    cn.create_note(world.db, world.pid, world.caregiver, "handover", "team only")           # never counts for the patient
    assert cn.unread_count(world.db, world.pid) == 2
    assert cn.mark_seen(world.db, world.pid) == 2 and cn.unread_count(world.db, world.pid) == 0
    assert all(x["seen"] for x in cn.list_notes(world.db, world.pid, "patient"))
    stranger = SimpleNamespace(id=world.caregiver.id + 999, role="caregiver", name="X")
    assert cn.soft_delete(world.db, a.id, stranger) is False                                 # only the author deletes
    assert cn.soft_delete(world.db, a.id, world.caregiver) is True
    assert [x["body"] for x in cn.list_notes(world.db, world.pid, "patient")] == ["second"]
    assert cn.soft_delete(world.db, a.id, world.caregiver) is False                          # already gone


def test_note_text_is_returned_verbatim_never_executed_server_side(world):
    n = cn.create_note(world.db, world.pid, world.caregiver, "message", "<script>alert(1)</script>")
    assert cn.serialize_note(n)["body"] == "<script>alert(1)</script>"                      # the screen escapes it (see UI contract tests)
