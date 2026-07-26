# ADR: Telemetry Ingest Durable Buffer Selection

**Status**: Superseded by L12-TEL-001 for deployed defaults
**Date**: 2026-04-10
**Decision update**: 2026-07-26
**Task**: TEL-002
**Owner**: Qwen
**Reviewer**: Codex

## Context

Per `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` §2.2, LEAN runtime must NOT directly high-concurrency sync-write to canonical Postgres telemetry tables. A durable buffer + async batch writer layer is required between event producers and Postgres.

The architecture document (§3.1 Layer C) lists three v1-acceptable options:
- **Redis Streams**
- **NATS JetStream**
- **Kafka**

## Decision Criteria

| Criterion | Weight | Description |
|---|---|---|
| Operational complexity | High | Pantheon v1 is a single-machine / small-cluster setup |
| Consumer count | Medium | v1 has 1 consumer (batch writer); v2+ may add replay/incident consumers |
| Throughput ceiling | High | Must handle burst telemetry from multiple concurrent strategies |
| Message durability | High | Critical order/fill/deploy events must NOT be lost |
| Replay capability | Medium | Must support time-window and event-type replay for incident investigation |
| Dependency footprint | High | v1 should not require external infrastructure if avoidable |

## Option Analysis

### Option 1: Redis Streams

| Aspect | Assessment |
|---|---|
| Operational complexity | **Low** — Redis is lightweight, already common in Python stacks |
| Consumer count | **Good** — consumer groups support multiple consumers |
| Throughput ceiling | **~100K msgs/sec** on a single node — sufficient for v1 |
| Message durability | **Good** — AOF + RDB persistence; configurable fsync |
| Replay capability | **Good** — `XREAD` with last-delivered ID; range scans by ID |
| Dependency footprint | **Low** — single binary, low RAM overhead for stream data |
| Tradeoff | Requires Redis server; not pure in-process |

### Option 2: NATS JetStream

| Aspect | Assessment |
|---|---|
| Operational complexity | **Medium** — NATS server required; JetStream adds config complexity |
| Consumer count | **Excellent** — native pull consumers, push consumers, queue groups |
| Throughput ceiling | **~1M msgs/sec** — highest of the three |
| Message durability | **Excellent** — file-based storage, ack-required delivery |
| Replay capability | **Excellent** — deliver policy: all, last, by start time, by sequence |
| Dependency footprint | **Medium** — separate server process |
| Tradeoff | Less common in Python ecosystem; more operational overhead for v1 |

### Option 3: Kafka

| Aspect | Assessment |
|---|---|
| Operational complexity | **High** — requires ZooKeeper (or KRaft), multi-process setup |
| Consumer count | **Excellent** — partitioned consumer groups, unlimited scaling |
| Throughput ceiling | **~10M+ msgs/sec** cluster-wide — overkill for v1 |
| Message durability | **Excellent** — replicated, durable on disk |
| Replay capability | **Excellent** — offset-based replay, time-based seek |
| Dependency footprint | **High** — JVM, ZooKeeper/KRaft, significant RAM |
| Tradeoff | Heavy operational burden for a v1 single-node deployment |

### Option 4: In-Memory Buffer (v1 transitional)

| Aspect | Assessment |
|---|---|
| Operational complexity | **None** — pure Python, no external dependency |
| Consumer count | **Single consumer** only (async writer task) |
| Throughput ceiling | **Limited by RAM** — bounded deque with overflow protection |
| Message durability | **None** — lost on process crash |
| Replay capability | **None** — no persistent history |
| Dependency footprint | **None** |
| Tradeoff | Not durable; suitable only as v1 shim until external buffer is available |

## L12-TEL-001 decision update

Process-crash recovery is now a formal requirement, and the Pantheon deployment
already supplies NATS with JetStream file storage. The deployed default is
therefore `NatsJetStreamBuffer`; `put()` waits for a server PubAck, and the
durable pull-consumer receipt remains unacknowledged until the canonical
Postgres write or an fsynced DLQ handoff completes.

`InMemoryBuffer` remains available only when explicitly selected for isolated
development and unit tests. It must not be used to claim durable telemetry
ingest. Redis remains an optional adapter, and Kafka remains out of scope.

## Historical v1 decision

### v1: In-Memory Bounded Buffer with External Adapter Interface

**Rationale**: Pantheon v1 is a development/research platform running on a single machine. The operational overhead of deploying Redis/NATS/Kafka outweighs the benefits at this scale.

**Approach**:
1. Implement a **bounded in-memory buffer** (`asyncio.Queue` with maxsize) as the v1 default
2. Define a **`DurableBuffer` protocol** (abstract base class) so v2 can swap in Redis Streams or NATS JetStream without changing ingest/writer code
3. Implement **Redis Streams adapter** as a ready-to-activate v2 path
4. The async batch writer, backpressure controller, and dead-letter queue are **buffer-agnostic** — they work against the protocol interface

**Activation criteria for v2 (Redis Streams)**:
- Pantheon deploys to a multi-node or production environment
- Telemetry volume exceeds 10K events/minute sustained
- Process crash recovery becomes a formal requirement
- External Redis is already part of the deployment stack for other reasons (caching, session store)

**Activation criteria for v3 (Kafka)**:
- Multiple independent consumers require partitioned replay
- Cross-service telemetry aggregation becomes a requirement
- Multi-region or multi-cluster deployment is needed

## Consequences

### Positive
- v1 ships with zero external dependencies for buffering
- Buffer swap is a single config change (`buffer_backend: memory` → `buffer_backend: redis`)
- All backpressure, batching, and dead-letter logic is tested against the protocol, not a specific backend
- No re-implementation needed when upgrading to Redis/Kafka

### Risks
- **Data loss on crash**: v1 in-memory buffer loses events if the ingest process crashes. Mitigation: treat the in-memory backend as a development/research shim only; deployments that require crash recovery must switch `buffer_backend` to Redis Streams before claiming durable ingest.
- **Memory pressure**: bounded queue with `maxsize` provides backpressure but does not persist. Mitigation: overflow routes directly to dead-letter queue with `buffer_overflow` tag.
- **No cross-process replay**: v1 cannot replay events from before the process started. Mitigation: canonical Postgres is the replay source; buffer is transport-only.

## Implementation Plan

| Step | Artifact | Description |
|---|---|---|
| 1 | `buffer.py` | `DurableBuffer` ABC + `InMemoryBuffer` + `RedisStreamBuffer` (v2-ready) |
| 2 | `batch_writer.py` | `AsyncBatchWriter` with micro-batching, retry, backoff, partition routing |
| 3 | `backpressure.py` | `BackpressureController` with adaptive concurrency, overflow handling |
| 4 | `dead_letter.py` | `DeadLetterQueue` with JSONL spill, diagnostic tags, replay support |
| 5 | `ingest_svc.py` | `TelemetryIngestService` tying all components together |
| 6 | Tests | Unit tests + smoke test for backpressure + replay |
