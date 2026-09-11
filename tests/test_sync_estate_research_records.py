from copy import deepcopy
import json

import pytest

from research_backend_client import BackendError
from sync_estate_research_records import read_handoff, sync_records, validate_handoff


def handoff(count=2):
    records = [{"kind": "research", "key": "fieldwork.r3.20260911.test." + str(i),
                "summary": "Synthetic archived observation", "expected_version": None,
                "data": {"model_eligible": False, "current_availability": None,
                         "source_observed_date": "2026-09-10", "synthetic": i}} for i in range(count)]
    return {"schema_version": "fieldwork_round3_handoff_v1", "target_project": "estate",
            "record_count": count, "records": records}


class MemoryBackend:
    project = "estate"

    def __init__(self):
        self.records = {}
        self.calls = []
        self.race = None
        self.lose_reply = False
        self.replace_after_put = False

    def json(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if path == "/ready":
            return {"status": "ready"}
        if "?limit=20" in path:
            return {"items": [], "next_cursor": None}
        if method == "GET":
            if path not in self.records:
                raise BackendError(404, "missing")
            return deepcopy(self.records[path])
        assert method == "PUT" and body["expected_version"] == 0
        if self.race:
            payload = body["payload"] if self.race == "same" else {"model_eligible": False, "synthetic": "other-writer"}
            self.records[path] = {"version": 7, "payload": deepcopy(payload)}
            raise BackendError(409, "race")
        assert path not in self.records, "Existing content must never be overwritten"
        self.records[path] = {"version": 1, "payload": deepcopy(body["payload"])}
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError("sensitive URL must not enter report")
        if self.replace_after_put:
            self.records[path] = {"version": 2, "payload": {"synthetic": "changed"}}
        return {"version": 1, "changed": True}


def writes(client):
    return [call for call in client.calls if call[0] == "PUT"]


def test_offline_default_is_payload_free_plan(tmp_path):
    target = tmp_path / "report.json"
    result = sync_records(handoff(), report_path=target)
    assert result["status"] == "planned" and not result["backend_write_attempted"]
    assert result["counts_by_kind"] == {"research": 2}
    assert result["next_key"] == "fieldwork.r3.20260911.test.0"
    assert "source_observed_date" not in target.read_text()


def test_live_dry_run_reads_selected_keys_without_writing():
    backend = MemoryBackend()
    result = sync_records(handoff(), client=backend)
    assert result["status"] == "verified_dry_run" and not writes(backend)
    assert all(item["status"] == "would_create" for item in result["items"])
    assert backend.calls[1][1] == "/records/research?limit=20"


def test_apply_preserves_payload_dates_and_repeat_reuses_existing_content():
    backend, document = MemoryBackend(), handoff()
    result = sync_records(document, client=backend, apply=True)
    assert result["confirmed_written"] == 2 and result["status"] == "complete"
    assert writes(backend)[0][2]["payload"] == document["records"][0]["data"]
    before = len(writes(backend))
    again = sync_records(document, client=backend, apply=True)
    assert again["already_present"] == 2 and again["confirmed_written"] == 0
    assert len(writes(backend)) == before


def test_existing_different_payload_stops_without_overwrite():
    backend, document = MemoryBackend(), handoff()
    path = "/records/research/" + document["records"][0]["key"]
    backend.records[path] = {"version": 8, "payload": {"model_eligible": False, "retained": "newer-research"}}
    before = deepcopy(backend.records)
    result = sync_records(document, client=backend, apply=True)
    assert result["status"] == "conflict" and result["items"][0]["confirmed_version"] == 8
    assert backend.records == before and not writes(backend)
    assert result["next_key"] == document["records"][0]["key"]


@pytest.mark.parametrize("race,expected", [("same", "complete"), ("different", "conflict")])
def test_409_rereads_and_only_reuses_identical_payload(race, expected):
    backend = MemoryBackend()
    backend.race = race
    result = sync_records(handoff(1), client=backend, apply=True)
    assert result["status"] == expected and len(writes(backend)) == 1
    assert result["items"][0]["confirmed_version"] == 7


def test_lost_write_reply_keeps_next_key_and_retry_reconciles_live_state(tmp_path):
    backend = MemoryBackend()
    backend.lose_reply = True
    target = tmp_path / "handoff.json"
    result = sync_records(handoff(), client=backend, apply=True, report_path=target)
    assert result["status"] == "failed" and result["error_code"] == "TimeoutError"
    assert result["items"][0]["status"] == "write_pending"
    assert result["next_key"] == "fieldwork.r3.20260911.test.0"
    assert "sensitive URL" not in target.read_text()
    again = sync_records(handoff(), client=backend, apply=True, report_path=target)
    assert again["status"] == "complete" and again["already_present"] == 1
    assert len(writes(backend)) == 2


def test_successful_put_is_not_reported_complete_after_concurrent_replacement():
    backend = MemoryBackend()
    backend.replace_after_put = True
    result = sync_records(handoff(), client=backend, apply=True)
    assert result["status"] == "conflict" and result["confirmed_written"] == 0
    assert result["items"][0]["status"] == "write_unconfirmed"
    assert len(writes(backend)) == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(target_project="investment"),
    lambda d: d["records"][0]["data"].update(model_eligible=True),
    lambda d: d["records"][0].update(expected_version=0),
    lambda d: d["records"][0].update(kind="documents"),
    lambda d: d["records"][1].update(key=d["records"][0]["key"]),
])
def test_invalid_or_unsafe_source_is_rejected_before_any_backend_call(mutation):
    document = handoff()
    mutation(document)
    with pytest.raises(ValueError):
        validate_handoff(document)


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path):
    path = tmp_path / "source.json"
    path.write_text('{"target_project":"estate","target_project":"investment"}')
    with pytest.raises(ValueError, match="Duplicate JSON"):
        read_handoff(path)


def test_different_input_cannot_replace_previous_handoff_report(tmp_path):
    report = tmp_path / "report.json"
    sync_records(handoff(1), report_path=report)
    before = report.read_bytes()
    with pytest.raises(ValueError, match="different input"):
        sync_records(handoff(2), report_path=report)
    assert report.read_bytes() == before
