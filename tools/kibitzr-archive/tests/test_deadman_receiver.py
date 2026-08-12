import importlib.util
import pathlib


SCRIPT = pathlib.Path(__file__).parents[3] / "deploy" / "deadman-receiver.py"
SPEC = importlib.util.spec_from_file_location("deadman_receiver", SCRIPT)
deadman = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deadman)


def test_heartbeat_updates_receiver_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(deadman.time, "time", lambda: 1000)

    deadman.receive(state, {"event": "collector-heartbeat", "instance_id": "a"})

    assert '"last_seen": 1000' in state.read_text()


def test_absent_heartbeat_alarms_off_host(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    alarms = []
    monkeypatch.setattr(deadman.time, "time", lambda: 5000)
    monkeypatch.setattr(deadman, "_alarm", lambda reason, event: alarms.append(reason))

    assert deadman.check(state, max_age=2400, repeat=21600)
    assert alarms == ["collector heartbeat absent for 2401 seconds"]


def test_alert_delivery_failure_alarms_immediately(tmp_path, monkeypatch):
    alarms = []
    monkeypatch.setattr(deadman, "_alarm", lambda reason, event: alarms.append((reason, event)))
    event = {"event": "alert-delivery-failed", "instance_id": "a"}

    deadman.receive(tmp_path / "state.json", event)

    assert alarms == [("collector could not deliver its health alert", event)]


def test_failed_alarm_is_persisted_for_checker_retry(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    event = {"event": "alert-delivery-failed", "instance_id": "a"}
    attempts = []

    def alarm(reason, body):
        attempts.append(body)
        if len(attempts) == 1:
            raise OSError("alarm destination down")

    monkeypatch.setattr(deadman, "_alarm", alarm)
    monkeypatch.setattr(deadman.time, "time", lambda: 1000)
    deadman.receive(state, {"event": "collector-heartbeat", "instance_id": "a"})
    try:
        deadman.receive(state, event)
    except OSError:
        pass
    deadman.check(state, max_age=2400, repeat=21600)

    assert attempts == [event, event]
    assert "pending_alert_failure" not in state.read_text()
