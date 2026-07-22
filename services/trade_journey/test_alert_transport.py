import json

from services.foundation import OutboxRecordStatus
from services.trade_journey.alert_transport import DataQualityAlertTransport
from services.trade_journey.slo_data_quality import AlertPath, DataQualityIncident


def _incident(**overrides):
    defaults = dict(
        code="journey_stalled", severity="high", message="test incident",
        metric_name="stalled_after_seconds", observed_value=100, target_value=90,
        alert_path=AlertPath(event_type="trade_journey.slo.journey_stalled"),
        journey_id="tj-1", tenant_id="tenant-a", environment="paper", evidence_ref="/x",
    )
    defaults.update(overrides)
    return DataQualityIncident(**defaults)


def test_publish_incident_writes_a_published_outbox_record(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    record = transport.publish_incident(_incident())
    assert record.status == OutboxRecordStatus.PUBLISHED
    assert record.event.event_type == "trade_journey.slo.journey_stalled"
    assert record.event.payload["journey_id"] == "tj-1"
    assert transport.outbox_path.exists()


def test_publish_incident_is_durable_across_transport_instances(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    transport.publish_incidents([_incident(code="orphan_identifier"), _incident(code="broker_reject")])

    reopened = DataQualityAlertTransport(data_dir=tmp_path)
    published = reopened.load_published()
    assert len(published) == 2
    assert {record.event.payload["code"] for record in published} == {"orphan_identifier", "broker_reject"}


def test_environment_scoped_incident_has_no_journey_id_but_still_publishes(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    aggregate_incident = _incident(
        code="materializer_lag_breach", journey_id=None, tenant_id=None,
        environment="canary", evidence_ref=None,
    )
    record = transport.publish_incident(aggregate_incident)
    assert record.event.aggregate_id == "aggregate:canary"


def test_broker_sandbox_environment_maps_safely_and_preserves_original_string(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    record = transport.publish_incident(_incident(environment="broker_sandbox"))
    # foundation's EnvironmentName has no "broker_sandbox" value; the trace
    # scope falls back safely but the original Trade Journey environment
    # string must survive untouched in the payload.
    assert record.event.payload["environment"] == "broker_sandbox"


def test_outbox_file_is_append_only_jsonl(tmp_path):
    transport = DataQualityAlertTransport(data_dir=tmp_path)
    transport.publish_incidents([_incident(), _incident(code="broker_reject")])
    lines = transport.outbox_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must be valid JSONL
