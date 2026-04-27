"""
Lineage Read Service — LIN-002

Provides an iterative, breadth-first graph traversal engine for deep lineage
queries. Avoids recursive SQL join timeout failures by:

1. Pre-building forward and reverse indexes on all semantic edge types.
2. Traversing the graph iteratively (no recursion depth limits).
3. Deduplicating visited nodes to prevent infinite loops from self-lineage edges.
4. Building projections from indexed lookups, not multi-table joins.

The service consumes the LIN-001A benchmark corpus and the LIN-001 read-model
contract to produce derived-only lineage projections within the L1 SLA budgets:

- runtime_binding_projection:  p95 < 500ms  (sync summary)
- capital_pool_projection:     p95 < 500ms  (sync summary)
- telemetry_event_trace:       p95 < 500ms  (sync summary)
- source_runtime_telemetry_trace:
                               operator-facing derived trace by trace_id
- forensic_plan_trace:         p95 < 5000ms (forensic / async-capable)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semantic edge constants (from LIN-001A prep packet)
# ---------------------------------------------------------------------------

EDGE_STRATEGY_SPEC_SOURCE = "strategy_spec.source_record"
EDGE_EXPERIMENT_STRATEGY = "experiment_run.strategy_spec"
EDGE_ARTIFACT_EXPERIMENT = "candidate_artifact.experiment_run"
EDGE_APPROVAL_TARGET = "approval_decision.registry_target"
EDGE_DEPLOYMENT_APPROVAL = "deployment_plan.approval_decision"
EDGE_DEPLOYMENT_ARTIFACT = "deployment_plan.artifact"
EDGE_DEPLOYMENT_POOL = "deployment_plan.capital_pool"
EDGE_DEPLOYMENT_PERSONA = "deployment_plan.persona_binding"
EDGE_RUNTIME_ARTIFACT = "runtime_binding.artifact"
EDGE_RUNTIME_POOL = "runtime_binding.capital_pool"
EDGE_RUNTIME_PLAN = "runtime_binding.deployment_plan"
EDGE_RUNTIME_PERSONA = "runtime_binding.persona_binding"
EDGE_RUNTIME_ROLLBACK = "runtime_binding.rollback_parent"
EDGE_TELEMETRY_BINDING = "telemetry_event.runtime_binding"
EDGE_TELEMETRY_PLAN = "telemetry_event.deployment_plan"
EDGE_TELEMETRY_POOL = "telemetry_event.capital_pool"
EDGE_TELEMETRY_PERSONA = "telemetry_event.persona_binding"
EDGE_INCIDENT_BINDING = "incident_case.runtime_binding"
EDGE_INCIDENT_TELEMETRY = "incident_case.telemetry_event"
EDGE_POSTMORTEM_INCIDENT = "postmortem.incident_case"
EDGE_EVOLUTION_POSTMORTEM = "evolution_decision.postmortem"
EDGE_EVOLUTION_INCIDENT = "evolution_decision.incident_case"
EDGE_EVOLUTION_TARGET = "evolution_decision.registry_target"
EDGE_BROKER_ORDER_BINDING = "broker_order_event.runtime_binding"
EDGE_BROKER_ORDER_PLAN = "broker_order_event.deployment_plan"
EDGE_BROKER_ORDER_TELEMETRY = "broker_order_event.telemetry_event"

# Node type constants
NODE_SOURCE_RECORD = "source_record"
NODE_STRATEGY_SPEC = "strategy_spec"
NODE_EXPERIMENT_RUN = "experiment_run"
NODE_CANDIDATE_ARTIFACT = "candidate_artifact"
NODE_APPROVAL_DECISION = "approval_decision"
NODE_CAPITAL_POOL = "capital_pool"
NODE_PERSONA_BINDING = "persona_capital_binding"
NODE_DEPLOYMENT_PLAN = "deployment_plan"
NODE_RUNTIME_BINDING = "runtime_binding"
NODE_TELEMETRY_EVENT = "telemetry_event"
NODE_BROKER_ORDER_EVENT = "broker_order_event"
NODE_INCIDENT_CASE = "incident_case"
NODE_POSTMORTEM = "postmortem"
NODE_EVOLUTION_DECISION = "evolution_decision"
NODE_ARTIFACT_REF = "artifact_ref"
NODE_RUNTIME_REF = "runtime_ref"
NODE_TRACE = "trace"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GraphNode:
    """A single node in the lineage graph."""
    node_type: str
    node_id: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed semantic edge in the lineage graph."""
    edge_type: str
    from_type: str
    from_id: str
    to_type: str
    to_id: str


@dataclass
class ProjectionResult:
    """Result of a lineage projection query."""
    target_type: str
    target_id: str
    projection_updated_at: Optional[str] = None
    upstream_chain: list[dict[str, Any]] = field(default_factory=list)
    downstream_chain: list[dict[str, Any]] = field(default_factory=list)
    conflict_markers: list[dict[str, Any]] = field(default_factory=list)
    visited_node_count: int = 0
    visited_edge_count: int = 0


# ---------------------------------------------------------------------------
# In-memory graph store
# ---------------------------------------------------------------------------


class LineageGraph:
    """
    In-memory lineage graph with forward and reverse indexes.

    Supports O(1) edge traversal in both directions by maintaining
    separate forward (outgoing) and reverse (incoming) adjacency lists
    keyed by semantic edge type.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}  # key: "{type}:{id}"
        self.edges: list[GraphEdge] = []
        # Forward index: edge_type -> from_id -> [edge]
        self.forward: dict[str, dict[str, list[GraphEdge]]] = defaultdict(lambda: defaultdict(list))
        # Reverse index: edge_type -> to_id -> [edge]
        self.reverse: dict[str, dict[str, list[GraphEdge]]] = defaultdict(lambda: defaultdict(list))
        # Type index: node_type -> [node_id]
        self.by_type: dict[str, list[str]] = defaultdict(list)

    # -- Node operations ---------------------------------------------------

    def _node_key(self, node_type: str, node_id: str) -> str:
        return f"{node_type}:{node_id}"

    def add_node(self, node_type: str, node_id: str, data: dict[str, Any]) -> None:
        key = self._node_key(node_type, node_id)
        if key not in self.nodes:
            self.nodes[key] = GraphNode(node_type=node_type, node_id=node_id, data=data)
            self.by_type[node_type].append(node_id)

    def get_node(self, node_type: str, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(self._node_key(node_type, node_id))

    # -- Edge operations ---------------------------------------------------

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)
        from_key = self._node_key(edge.from_type, edge.from_id)
        to_key = self._node_key(edge.to_type, edge.to_id)
        self.forward[edge.edge_type][from_key].append(edge)
        self.reverse[edge.edge_type][to_key].append(edge)

    def outgoing(self, edge_type: str, node_type: str, node_id: str) -> list[GraphEdge]:
        """Return outgoing edges of a specific semantic type from a node."""
        key = self._node_key(node_type, node_id)
        return list(self.forward.get(edge_type, {}).get(key, []))

    def incoming(self, edge_type: str, node_type: str, node_id: str) -> list[GraphEdge]:
        """Return incoming edges of a specific semantic type to a node."""
        key = self._node_key(node_type, node_id)
        return list(self.reverse.get(edge_type, {}).get(key, []))

    def all_outgoing(self, node_type: str, node_id: str) -> list[GraphEdge]:
        """Return all outgoing edges from a node (any semantic type)."""
        key = self._node_key(node_type, node_id)
        result: list[GraphEdge] = []
        for edge_map in self.forward.values():
            result.extend(edge_map.get(key, []))
        return result

    def all_incoming(self, node_type: str, node_id: str) -> list[GraphEdge]:
        """Return all incoming edges to a node (any semantic type)."""
        key = self._node_key(node_type, node_id)
        result: list[GraphEdge] = []
        for edge_map in self.reverse.values():
            result.extend(edge_map.get(key, []))
        return result

    def nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """Return all nodes of a given type."""
        return [
            self.nodes[self._node_key(node_type, nid)]
            for nid in self.by_type.get(node_type, [])
            if self._node_key(node_type, nid) in self.nodes
        ]


# ---------------------------------------------------------------------------
# Iterative graph traverser
# ---------------------------------------------------------------------------


class LineageTraverser:
    """
    Iterative breadth-first traverser for the lineage graph.

    This is the core performance path. Instead of recursive SQL joins that
    can timeout on deep graphs, this traverser:

    1. Uses a deque-based BFS with explicit visited set.
    2. Follows both incoming (upstream) and outgoing (downstream) edges.
    3. Stops at configurable depth limits per SLA bucket.
    4. Produces ordered chain lists suitable for projection payloads.
    """

    def __init__(self, graph: LineageGraph) -> None:
        self.graph = graph

    def traverse_from(
        self,
        node_type: str,
        node_id: str,
        *,
        upstream_depth: int = 10,
        downstream_depth: int = 10,
        max_visited: int = 10_000,
    ) -> ProjectionResult:
        """
        Traverse the lineage graph from a starting node.

        Returns a ProjectionResult with upstream_chain, downstream_chain,
        and conflict_markers populated.

        Parameters
        ----------
        node_type : str
            The type of the starting node.
        node_id : str
            The ID of the starting node.
        upstream_depth : int
            Maximum depth for upstream (incoming edge) traversal.
        downstream_depth : int
            Maximum depth for downstream (outgoing edge) traversal.
        max_visited : int
            Safety limit on total nodes visited to prevent runaway traversals.
        """
        start_key = self.graph._node_key(node_type, node_id)
        start_node = self.graph.nodes.get(start_key)
        if start_node is None:
            log.warning(f"Node not found: {start_key}")
            return ProjectionResult(
                target_type=node_type,
                target_id=node_id,
                conflict_markers=[{"code": "node_not_found", "node_key": start_key}],
            )

        visited: set[str] = set()
        upstream_chain: list[dict[str, Any]] = []
        downstream_chain: list[dict[str, Any]] = []
        conflict_markers: list[dict[str, Any]] = []
        total_visited = 0

        # --- Upstream traversal (follow dependency edges: from→to) ---------
        # Edges point from dependent TO dependency, so outgoing = upstream in lineage terms
        upstream_visited: set[str] = set()
        self._bfs_outgoing(
            node_type=node_type,
            node_id=node_id,
            visited=upstream_visited,
            chain=upstream_chain,
            max_depth=upstream_depth,
            max_visits=min(max_visited, 5000),
            conflicts=conflict_markers,
        )

        # --- Downstream traversal (follow reverse edges: to→from) ----------
        # Reverse edges point from dependency TO dependent, so incoming = downstream
        downstream_visited: set[str] = set()
        self._bfs_incoming(
            node_type=node_type,
            node_id=node_id,
            visited=downstream_visited,
            chain=downstream_chain,
            max_depth=downstream_depth,
            max_visits=min(max_visited, 5000),
            conflicts=conflict_markers,
        )

        total_visited = len(upstream_visited) + len(downstream_visited)

        # Detect rollback-related conflict markers from the graph
        self._detect_rollback_conflicts(
            node_type=node_type,
            node_id=node_id,
            conflicts=conflict_markers,
        )

        # Detect alias drift conflicts for telemetry events
        self._detect_alias_conflicts(
            node_type=node_type,
            node_id=node_id,
            conflicts=conflict_markers,
        )

        # Compute the freshest timestamp from all visited nodes
        projection_updated_at = self._freshest_timestamp(
            upstream_visited, downstream_visited, start_key
        )

        return ProjectionResult(
            target_type=node_type,
            target_id=node_id,
            projection_updated_at=projection_updated_at,
            upstream_chain=upstream_chain,
            downstream_chain=downstream_chain,
            conflict_markers=conflict_markers,
            visited_node_count=total_visited,
            visited_edge_count=total_visited,  # approximate
        )

    def _bfs_incoming(
        self,
        node_type: str,
        node_id: str,
        visited: set[str],
        chain: list[dict[str, Any]],
        max_depth: int,
        max_visits: int,
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Breadth-first traversal following incoming (upstream) edges."""
        start_key = self.graph._node_key(node_type, node_id)
        queue: deque[tuple[str, int]] = deque()  # (node_key, depth)

        # Seed with direct upstream neighbors
        for edge in self.graph.all_incoming(node_type, node_id):
            target_key = self.graph._node_key(edge.from_type, edge.from_id)
            queue.append((target_key, 1))

        while queue and len(visited) < max_visits:
            key, depth = queue.popleft()
            if key in visited or depth > max_depth:
                continue
            visited.add(key)

            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            ntype, nid = parts
            node = self.graph.get_node(ntype, nid)
            if node is None:
                continue

            # Add to chain (upstream = earlier in the list)
            chain.insert(0, self._node_to_chain_item(node))

            # Enqueue further upstream neighbors
            for edge in self.graph.all_incoming(ntype, nid):
                next_key = self.graph._node_key(edge.from_type, edge.from_id)
                if next_key not in visited:
                    queue.append((next_key, depth + 1))

    def _bfs_outgoing(
        self,
        node_type: str,
        node_id: str,
        visited: set[str],
        chain: list[dict[str, Any]],
        max_depth: int,
        max_visits: int,
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Breadth-first traversal following outgoing (downstream) edges."""
        start_key = self.graph._node_key(node_type, node_id)
        queue: deque[tuple[str, int]] = deque()

        # Seed with direct downstream neighbors
        for edge in self.graph.all_outgoing(node_type, node_id):
            target_key = self.graph._node_key(edge.to_type, edge.to_id)
            queue.append((target_key, 1))

        while queue and len(visited) < max_visits:
            key, depth = queue.popleft()
            if key in visited or depth > max_depth:
                continue
            visited.add(key)

            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            ntype, nid = parts
            node = self.graph.get_node(ntype, nid)
            if node is None:
                continue

            chain.append(self._node_to_chain_item(node))

            # Enqueue further downstream neighbors
            for edge in self.graph.all_outgoing(ntype, nid):
                next_key = self.graph._node_key(edge.to_type, edge.to_id)
                if next_key not in visited:
                    queue.append((next_key, depth + 1))

    def _node_to_chain_item(self, node: GraphNode) -> dict[str, Any]:
        """Convert a GraphNode to a chain item dict."""
        item = {"type": node.node_type, "id": node.node_id}
        # Carry through useful data fields
        data = node.data
        if "event_type" in data:
            item["event_type"] = data["event_type"]
        if "status" in data:
            item["status"] = data["status"]
        if "rollback_parent" in data and data["rollback_parent"]:
            item["rollback_parent"] = data["rollback_parent"]
        if "rollback_action_type" in data and data["rollback_action_type"]:
            item["rollback_action_type"] = data["rollback_action_type"]
        if "event_produced_at" in data:
            item["event_produced_at"] = data["event_produced_at"]
        if "created_at" in data:
            item["created_at"] = data["created_at"]
        if "strategy_id" in data:
            item["strategy_id"] = data["strategy_id"]
        if "run_id" in data:
            item["run_id"] = data["run_id"]
        if "approval_decision_id" in data:
            item["approval_decision_id"] = data["approval_decision_id"]
        if "decision_state" in data:
            item["decision_state"] = data["decision_state"]
        if "action_type" in data:
            item["action_type"] = data["action_type"]
        if "broker" in data:
            item["broker"] = data["broker"]
        if "order_id" in data:
            item["order_id"] = data["order_id"]
        if "order_status" in data:
            item["order_status"] = data["order_status"]
        return item

    def _detect_rollback_conflicts(
        self,
        node_type: str,
        node_id: str,
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Detect rollback-related conflicts from the neighborhood of a node."""
        # Check for rollback_parent self-lineage
        for edge in self.graph.outgoing(EDGE_RUNTIME_ROLLBACK, node_type, node_id):
            conflicts.append({
                "code": "rollback_parent",
                "id": edge.to_id,
            })
            # Also capture the action type if available
            node = self.graph.get_node(node_type, node_id)
            if node and node.data.get("rollback_action_type"):
                conflicts.append({
                    "code": "rollback_action_type",
                    "id": node.data["rollback_action_type"],
                })

        # Check all runtime binding nodes in the graph for rollback edges
        # This catches rollback markers reachable from the traversal
        for edge in self.graph.edges:
            if edge.edge_type == EDGE_RUNTIME_ROLLBACK:
                # Only add if we haven't already added this specific marker
                marker_id = edge.to_id
                if not any(
                    c.get("code") == "rollback_parent" and c.get("id") == marker_id
                    for c in conflicts
                ):
                    # Check if this rollback edge is reachable from the starting node
                    # (this is a simplification — in production, track reachability)
                    pass

    def _detect_alias_conflicts(
        self,
        node_type: str,
        node_id: str,
        conflicts: list[dict[str, Any]],
    ) -> None:
        """
        Detect alias drift conflicts by reusing the feedback_adapter normalization logic.

        For telemetry events, checks:
        - binding_id vs runtime_binding_id  -> runtime_binding_alias_mismatch
        - plan_id vs deployment_plan_id     -> deployment_plan_alias_mismatch
        - environment vs deployment_stage   -> deployment_stage_alias_mismatch
        - artifact_version vs target.artifact_version -> artifact_version_target_mismatch
        """
        if node_type != NODE_TELEMETRY_EVENT:
            return

        event_node = self.graph.get_node(NODE_TELEMETRY_EVENT, node_id)
        if not event_node:
            return

        d = event_node.data

        # runtime_binding alias mismatch
        rb_id = d.get("runtime_binding_id")
        binding_id = d.get("binding_id")
        if rb_id and binding_id and rb_id != binding_id:
            conflicts.append({
                "code": "runtime_binding_alias_mismatch",
                "left": rb_id,
                "right": binding_id,
            })

        # deployment_plan alias mismatch
        dp_id = d.get("deployment_plan_id")
        plan_id = d.get("plan_id")
        if dp_id and plan_id and dp_id != plan_id:
            conflicts.append({
                "code": "deployment_plan_alias_mismatch",
                "left": dp_id,
                "right": plan_id,
            })

        # deployment_stage alias mismatch
        stage = d.get("deployment_stage")
        env = d.get("environment")
        if stage and env and stage != env:
            conflicts.append({
                "code": "deployment_stage_alias_mismatch",
                "left": stage,
                "right": env,
            })

        # artifact_version target mismatch
        top_level_av = d.get("artifact_version")
        target_av = d.get("target", {}).get("artifact_version") if isinstance(d.get("target"), dict) else None
        if top_level_av and target_av and top_level_av != target_av:
            conflicts.append({
                "code": "artifact_version_target_mismatch",
                "left": top_level_av,
                "right": target_av,
            })

    def _freshest_timestamp(
        self,
        upstream_visited: set[str],
        downstream_visited: set[str],
        start_key: str,
    ) -> Optional[str]:
        """Return the most recent timestamp among all visited nodes."""
        all_keys = upstream_visited | downstream_visited | {start_key}
        freshest: Optional[str] = None
        for key in all_keys:
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            ntype, nid = parts
            node = self.graph.get_node(ntype, nid)
            if node is None:
                continue
            for ts_field in ("created_at", "event_produced_at", "effective_at"):
                ts = node.data.get(ts_field)
                if ts and (freshest is None or ts > freshest):
                    freshest = ts
        return freshest


# ---------------------------------------------------------------------------
# Projection builders (query-family specific)
# ---------------------------------------------------------------------------


class ProjectionBuilder:
    """
    Builds query-family-specific projection payloads from traversal results.

    Each method produces a payload matching the LIN-001A benchmark corpus
    shape for its query family.
    """

    @staticmethod
    def runtime_binding_projection(
        traverser: LineageTraverser,
        binding_id: str,
    ) -> dict[str, Any]:
        result = traverser.traverse_from(
            NODE_RUNTIME_BINDING, binding_id,
            upstream_depth=5, downstream_depth=10,
        )
        return _enrich_runtime_binding_projection(result, traverser.graph)

    @staticmethod
    def capital_pool_projection(
        traverser: LineageTraverser,
        pool_id: str,
    ) -> dict[str, Any]:
        # Capital pool is a root node — all related nodes point TO it via FK edges.
        # We need to traverse incoming edges for "downstream" discovery.
        result = traverser.traverse_from(
            NODE_CAPITAL_POOL, pool_id,
            upstream_depth=1, downstream_depth=1,
        )
        # Manually build downstream chain from all nodes that reference this pool
        graph = traverser.graph
        downstream_chain: list[dict[str, Any]] = []
        visited_pool_refs: set[str] = set()

        # Find all persona bindings pointing to this pool
        for edge in graph.incoming("persona_binding.capital_pool", NODE_CAPITAL_POOL, pool_id):
            binding_node = graph.get_node(NODE_PERSONA_BINDING, edge.from_id)
            if binding_node and edge.from_id not in visited_pool_refs:
                visited_pool_refs.add(edge.from_id)
                downstream_chain.append({"type": NODE_PERSONA_BINDING, "id": edge.from_id})

        # Find all deployment plans pointing to this pool
        for edge in graph.incoming(EDGE_DEPLOYMENT_POOL, NODE_CAPITAL_POOL, pool_id):
            plan_node = graph.get_node(NODE_DEPLOYMENT_PLAN, edge.from_id)
            if plan_node and edge.from_id not in visited_pool_refs:
                visited_pool_refs.add(edge.from_id)
                downstream_chain.append({"type": NODE_DEPLOYMENT_PLAN, "id": edge.from_id})

        # Find all runtime bindings pointing to this pool
        for edge in graph.incoming(EDGE_RUNTIME_POOL, NODE_CAPITAL_POOL, pool_id):
            rb_node = graph.get_node(NODE_RUNTIME_BINDING, edge.from_id)
            if rb_node and edge.from_id not in visited_pool_refs:
                visited_pool_refs.add(edge.from_id)
                downstream_chain.append({"type": NODE_RUNTIME_BINDING, "id": edge.from_id})
                # Add runtime_ref
                rid = rb_node.data.get("runtime_id")
                if rid and rid not in visited_pool_refs:
                    visited_pool_refs.add(rid)
                    downstream_chain.append({"type": "runtime_ref", "id": rid})

        # Find all telemetry events pointing to this pool
        for edge in graph.incoming(EDGE_TELEMETRY_POOL, NODE_CAPITAL_POOL, pool_id):
            evt_node = graph.get_node(NODE_TELEMETRY_EVENT, edge.from_id)
            if evt_node and edge.from_id not in visited_pool_refs:
                visited_pool_refs.add(edge.from_id)
                downstream_chain.append({"type": NODE_TELEMETRY_EVENT, "id": edge.from_id})

        result.downstream_chain = downstream_chain
        return _enrich_capital_pool_projection(result, graph)

    @staticmethod
    def telemetry_event_trace(
        traverser: LineageTraverser,
        event_id: str,
    ) -> dict[str, Any]:
        result = traverser.traverse_from(
            NODE_TELEMETRY_EVENT, event_id,
            upstream_depth=5, downstream_depth=3,
        )
        return _enrich_telemetry_event_trace(result, traverser.graph)

    @staticmethod
    def forensic_plan_trace(
        traverser: LineageTraverser,
        plan_id: str,
    ) -> dict[str, Any]:
        graph = traverser.graph
        plan_node = graph.get_node(NODE_DEPLOYMENT_PLAN, plan_id)

        upstream_chain: list[dict[str, Any]] = []
        downstream_chain: list[dict[str, Any]] = []
        conflict_markers: list[dict[str, Any]] = []
        visited_ids: set[str] = set()

        # --- Upstream: follow FK edges from plan to its dependencies ---------
        if plan_node:
            # Pool
            pool_id = plan_node.data.get("capital_pool_id")
            if pool_id:
                pool_node = graph.get_node(NODE_CAPITAL_POOL, pool_id)
                if pool_node:
                    upstream_chain.append({"type": NODE_CAPITAL_POOL, "id": pool_id})
                    visited_ids.add(pool_id)

            # Persona binding
            pb_id = plan_node.data.get("binding_id")  # DeploymentPlan.binding_id
            if pb_id:
                pb_node = graph.get_node(NODE_PERSONA_BINDING, pb_id)
                if pb_node:
                    upstream_chain.append({"type": NODE_PERSONA_BINDING, "id": pb_id})
                    visited_ids.add(pb_id)

        # --- Downstream: find all nodes that reference this plan --------------
        # Runtime bindings pointing to this plan
        for edge in graph.incoming(EDGE_RUNTIME_PLAN, NODE_DEPLOYMENT_PLAN, plan_id):
            rb_node = graph.get_node(NODE_RUNTIME_BINDING, edge.from_id)
            if rb_node and edge.from_id not in visited_ids:
                visited_ids.add(edge.from_id)
                downstream_chain.append({
                    "type": NODE_RUNTIME_BINDING,
                    "id": edge.from_id,
                    "status": rb_node.data.get("status"),
                })

        # Telemetry events pointing to this plan
        for edge in graph.incoming(EDGE_TELEMETRY_PLAN, NODE_DEPLOYMENT_PLAN, plan_id):
            evt_node = graph.get_node(NODE_TELEMETRY_EVENT, edge.from_id)
            if evt_node and edge.from_id not in visited_ids:
                visited_ids.add(edge.from_id)
                downstream_chain.append({
                    "type": NODE_TELEMETRY_EVENT,
                    "id": edge.from_id,
                    "event_type": evt_node.data.get("event_type"),
                })

        result = ProjectionResult(
            target_type=NODE_DEPLOYMENT_PLAN,
            target_id=plan_id,
            upstream_chain=upstream_chain,
            downstream_chain=downstream_chain,
            conflict_markers=conflict_markers,
        )
        return _enrich_forensic_plan_trace(result, graph)

    @staticmethod
    def source_runtime_telemetry_trace(
        traverser: LineageTraverser,
        trace_id: str,
    ) -> dict[str, Any]:
        return _build_source_runtime_telemetry_trace(traverser.graph, trace_id)


def _artifact_ref(artifact_id: str, artifact_version: str) -> str:
    return f"{artifact_id}@{artifact_version}"


def _record_identifier(record: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _timestamp_value(data: dict[str, Any]) -> str:
    for key in ("event_produced_at", "created_at", "effective_at", "executed_at"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def _node_sort_key(node: GraphNode) -> tuple[str, str, str]:
    return (_timestamp_value(node.data), node.node_type, node.node_id)


def _chain_item(node: GraphNode, *, include_data: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {"type": node.node_type, "id": node.node_id}
    data = node.data
    passthrough_fields = (
        "event_type",
        "status",
        "decision_state",
        "action_type",
        "strategy_id",
        "run_id",
        "source_id",
        "approval_decision_id",
        "plan_id",
        "binding_id",
        "runtime_id",
        "runtime_binding_id",
        "deployment_plan_id",
        "capital_pool_id",
        "persona_capital_binding_id",
        "artifact_id",
        "artifact_version",
        "trace_id",
        "request_id",
        "broker",
        "account_ref",
        "order_id",
        "order_status",
        "lifecycle_state",
        "rollback_parent",
        "rollback_action_type",
        "created_at",
        "event_produced_at",
        "effective_at",
        "executed_at",
    )
    for field_name in passthrough_fields:
        value = data.get(field_name)
        if value not in (None, ""):
            item[field_name] = value
    if include_data:
        item["data"] = dict(data)
    return item


def _append_unique(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = (item.get("type"), item.get("id"))
    if not any((existing.get("type"), existing.get("id")) == key for existing in items):
        items.append(item)


def _add_missing_edge(
    missing_edges: list[dict[str, Any]],
    conflict_markers: list[dict[str, Any]],
    *,
    edge_type: str,
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: str,
) -> None:
    marker = {
        "edge_type": edge_type,
        "from_type": from_type,
        "from_id": from_id,
        "to_type": to_type,
        "to_id": to_id,
    }
    if marker not in missing_edges:
        missing_edges.append(marker)
    conflict = {
        "code": "missing_lineage_edge",
        **marker,
    }
    if conflict not in conflict_markers:
        conflict_markers.append(conflict)


def _known_or_missing(
    graph: LineageGraph,
    missing_edges: list[dict[str, Any]],
    conflict_markers: list[dict[str, Any]],
    *,
    edge_type: str,
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: Optional[str],
) -> Optional[GraphNode]:
    if not to_id:
        return None
    node = graph.get_node(to_type, to_id)
    if node is None:
        _add_missing_edge(
            missing_edges,
            conflict_markers,
            edge_type=edge_type,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
        )
    return node


def _nested_value(data: dict[str, Any], outer: str, inner: str) -> Optional[Any]:
    value = data.get(outer)
    if isinstance(value, dict):
        nested = value.get(inner)
        if nested not in (None, ""):
            return nested
    return None


_ORDER_LIFECYCLE_EVENT_TYPES = {
    "fill_observation",
    "order_rejection",
    "order_filled",
    "order_partially_filled",
    "order_canceled",
    "order_cancelled",
    "order_submitted",
    "order_acknowledged",
    "slippage_observation",
}


def _build_source_runtime_telemetry_trace(
    graph: LineageGraph,
    trace_id: str,
) -> dict[str, Any]:
    """Build the first operator-facing source->runtime->telemetry trace.

    The payload is derived from owner-written refs only. Missing referenced
    nodes are surfaced as explicit missing_edges/conflict_markers instead of
    being inferred or backfilled.
    """
    telemetry_nodes = sorted(
        [
            node
            for node in graph.nodes_by_type(NODE_TELEMETRY_EVENT)
            if (
                node.data.get("trace_id") == trace_id
                or _nested_value(node.data, "authority_refs", "trace_id") == trace_id
            )
        ],
        key=_node_sort_key,
    )

    if not telemetry_nodes:
        return {
            "target_type": NODE_TRACE,
            "target_id": trace_id,
            "derived_only": True,
            "projection_updated_at": None,
            "upstream_chain": [],
            "downstream_chain": [],
            "conflict_markers": [
                {"code": "node_not_found", "node_key": f"{NODE_TRACE}:{trace_id}"}
            ],
            "missing_edges": [],
            "operator_trace": {
                "source_chain": [],
                "deployment_chain": [],
                "runtime_chain": [],
                "broker_order_lifecycle": [],
                "telemetry_events": [],
                "evolution_refs": [],
            },
            "refs": _operator_trace_refs(trace_id=trace_id),
            "telemetry_event_count": 0,
            "broker_order_event_count": 0,
            "evolution_decision_count": 0,
            "_meta": {"visited_node_count": 0, "visited_edge_count": 0},
        }

    conflict_markers: list[dict[str, Any]] = []
    missing_edges: list[dict[str, Any]] = []
    source_chain: list[dict[str, Any]] = []
    deployment_chain: list[dict[str, Any]] = []
    runtime_chain: list[dict[str, Any]] = []
    telemetry_events: list[dict[str, Any]] = []
    broker_order_lifecycle: list[dict[str, Any]] = []
    incident_refs: list[dict[str, Any]] = []
    postmortem_refs: list[dict[str, Any]] = []
    evolution_refs: list[dict[str, Any]] = []
    visited_nodes: dict[tuple[str, str], GraphNode] = {}

    def remember(node: Optional[GraphNode]) -> Optional[GraphNode]:
        if node is not None:
            visited_nodes[(node.node_type, node.node_id)] = node
        return node

    binding_ids: set[str] = set()
    plan_ids: set[str] = set()
    pool_ids: set[str] = set()
    persona_binding_ids: set[str] = set()
    artifact_ids: set[str] = set()
    artifact_refs: set[str] = set()
    strategy_ids: set[str] = set()
    request_ids: set[str] = set()
    telemetry_event_ids: set[str] = {node.node_id for node in telemetry_nodes}

    for event_node in telemetry_nodes:
        remember(event_node)
        event_data = event_node.data
        telemetry_item = _chain_item(event_node)
        _append_unique(telemetry_events, telemetry_item)

        request_id = event_data.get("request_id") or _nested_value(event_data, "authority_refs", "request_id")
        if request_id:
            request_ids.add(str(request_id))

        binding_id = event_data.get("runtime_binding_id") or event_data.get("binding_id")
        if binding_id:
            binding_ids.add(str(binding_id))
            remember(_known_or_missing(
                graph,
                missing_edges,
                conflict_markers,
                edge_type=EDGE_TELEMETRY_BINDING,
                from_type=NODE_TELEMETRY_EVENT,
                from_id=event_node.node_id,
                to_type=NODE_RUNTIME_BINDING,
                to_id=str(binding_id),
            ))

        plan_id = event_data.get("deployment_plan_id") or event_data.get("plan_id")
        if plan_id:
            plan_ids.add(str(plan_id))
            remember(_known_or_missing(
                graph,
                missing_edges,
                conflict_markers,
                edge_type=EDGE_TELEMETRY_PLAN,
                from_type=NODE_TELEMETRY_EVENT,
                from_id=event_node.node_id,
                to_type=NODE_DEPLOYMENT_PLAN,
                to_id=str(plan_id),
            ))

        if event_data.get("capital_pool_id"):
            pool_ids.add(str(event_data["capital_pool_id"]))
        if event_data.get("persona_capital_binding_id"):
            persona_binding_ids.add(str(event_data["persona_capital_binding_id"]))
        if event_data.get("artifact_id"):
            artifact_ids.add(str(event_data["artifact_id"]))
        if event_data.get("artifact_id") and event_data.get("artifact_version"):
            artifact_refs.add(_artifact_ref(str(event_data["artifact_id"]), str(event_data["artifact_version"])))
        strategy_id = event_data.get("strategy_id") or _nested_value(event_data, "target", "strategy_id")
        if strategy_id:
            strategy_ids.add(str(strategy_id))

        event_type = str(event_data.get("event_type") or "")
        if (
            event_type in _ORDER_LIFECYCLE_EVENT_TYPES
            or event_data.get("broker")
            or event_data.get("order_id")
            or event_data.get("signal_id")
        ):
            lifecycle_item = dict(telemetry_item)
            lifecycle_item["source"] = NODE_TELEMETRY_EVENT
            _append_unique(broker_order_lifecycle, lifecycle_item)

    for binding_id in sorted(binding_ids):
        binding_node = remember(graph.get_node(NODE_RUNTIME_BINDING, binding_id))
        if binding_node is None:
            continue
        binding_data = binding_node.data
        _append_unique(runtime_chain, _chain_item(binding_node))
        if binding_data.get("runtime_id"):
            _append_unique(runtime_chain, {"type": NODE_RUNTIME_REF, "id": binding_data["runtime_id"]})
        if binding_data.get("plan_id"):
            plan_ids.add(str(binding_data["plan_id"]))
        if binding_data.get("capital_pool_id"):
            pool_ids.add(str(binding_data["capital_pool_id"]))
        if binding_data.get("persona_capital_binding_id"):
            persona_binding_ids.add(str(binding_data["persona_capital_binding_id"]))
        if binding_data.get("artifact_id"):
            artifact_ids.add(str(binding_data["artifact_id"]))
        if binding_data.get("artifact_id") and binding_data.get("artifact_version"):
            artifact_refs.add(_artifact_ref(str(binding_data["artifact_id"]), str(binding_data["artifact_version"])))

    for plan_id in sorted(plan_ids):
        plan_node = remember(graph.get_node(NODE_DEPLOYMENT_PLAN, plan_id))
        if plan_node is None:
            continue
        plan_data = plan_node.data
        approval_id = plan_data.get("approval_decision_id")
        if approval_id:
            approval_node = remember(_known_or_missing(
                graph,
                missing_edges,
                conflict_markers,
                edge_type=EDGE_DEPLOYMENT_APPROVAL,
                from_type=NODE_DEPLOYMENT_PLAN,
                from_id=plan_id,
                to_type=NODE_APPROVAL_DECISION,
                to_id=str(approval_id),
            ))
            if approval_node is not None:
                _append_unique(deployment_chain, _chain_item(approval_node))
        _append_unique(deployment_chain, _chain_item(plan_node))
        if plan_data.get("capital_pool_id"):
            pool_ids.add(str(plan_data["capital_pool_id"]))
        if plan_data.get("binding_id"):
            persona_binding_ids.add(str(plan_data["binding_id"]))
        if plan_data.get("artifact_id"):
            artifact_ids.add(str(plan_data["artifact_id"]))
            remember(_known_or_missing(
                graph,
                missing_edges,
                conflict_markers,
                edge_type=EDGE_DEPLOYMENT_ARTIFACT,
                from_type=NODE_DEPLOYMENT_PLAN,
                from_id=plan_id,
                to_type=NODE_CANDIDATE_ARTIFACT,
                to_id=str(plan_data["artifact_id"]),
            ))
        if plan_data.get("artifact_id") and plan_data.get("artifact_version"):
            artifact_refs.add(_artifact_ref(str(plan_data["artifact_id"]), str(plan_data["artifact_version"])))
        if plan_data.get("strategy_id"):
            strategy_ids.add(str(plan_data["strategy_id"]))

    for pool_id in sorted(pool_ids):
        pool_node = remember(graph.get_node(NODE_CAPITAL_POOL, pool_id))
        if pool_node is not None:
            _append_unique(deployment_chain, _chain_item(pool_node))

    for persona_binding_id in sorted(persona_binding_ids):
        persona_node = remember(graph.get_node(NODE_PERSONA_BINDING, persona_binding_id))
        if persona_node is not None:
            _append_unique(deployment_chain, _chain_item(persona_node))

    for artifact_id in sorted(artifact_ids):
        artifact_node = remember(graph.get_node(NODE_CANDIDATE_ARTIFACT, artifact_id))
        if artifact_node is not None:
            _append_research_chain_for_artifact(
                graph,
                artifact_node,
                source_chain,
                missing_edges,
                conflict_markers,
                remember,
            )
        else:
            for artifact_ref in sorted(ref for ref in artifact_refs if ref.startswith(f"{artifact_id}@")):
                _append_unique(source_chain, {"type": NODE_ARTIFACT_REF, "id": artifact_ref})

    for strategy_id in sorted(strategy_ids):
        if not any(item.get("type") == NODE_STRATEGY_SPEC and item.get("id") == strategy_id for item in source_chain):
            strategy_node = remember(graph.get_node(NODE_STRATEGY_SPEC, strategy_id))
            if strategy_node is not None:
                _append_research_chain_for_strategy(
                    graph,
                    strategy_node,
                    source_chain,
                    missing_edges,
                    conflict_markers,
                    remember,
                )

    broker_order_nodes = _related_broker_order_nodes(
        graph,
        trace_id=trace_id,
        binding_ids=binding_ids,
        plan_ids=plan_ids,
        telemetry_event_ids=telemetry_event_ids,
    )
    for node in broker_order_nodes:
        remember(node)
        _append_unique(broker_order_lifecycle, _chain_item(node))

    incident_nodes = _related_incident_nodes(
        graph,
        trace_id=trace_id,
        binding_ids=binding_ids,
        telemetry_event_ids=telemetry_event_ids,
    )
    incident_ids = {node.node_id for node in incident_nodes}
    for node in incident_nodes:
        remember(node)
        _append_unique(incident_refs, _chain_item(node))

    postmortem_nodes = _related_postmortem_nodes(graph, incident_ids)
    postmortem_ids = {node.node_id for node in postmortem_nodes}
    for node in postmortem_nodes:
        remember(node)
        _append_unique(postmortem_refs, _chain_item(node))

    evolution_nodes = _related_evolution_nodes(
        graph,
        trace_id=trace_id,
        artifact_ids=artifact_ids,
        strategy_ids=strategy_ids,
        pool_ids=pool_ids,
        persona_binding_ids=persona_binding_ids,
        incident_ids=incident_ids,
        postmortem_ids=postmortem_ids,
        telemetry_event_ids=telemetry_event_ids,
        plan_ids=plan_ids,
        binding_ids=binding_ids,
    )
    for node in evolution_nodes:
        remember(node)
        _append_unique(evolution_refs, _chain_item(node))

    upstream_chain: list[dict[str, Any]] = []
    downstream_chain: list[dict[str, Any]] = []
    for item in [*source_chain, *deployment_chain, *runtime_chain]:
        _append_unique(upstream_chain, item)
    for item in [*telemetry_events, *broker_order_lifecycle, *incident_refs, *postmortem_refs, *evolution_refs]:
        _append_unique(downstream_chain, item)

    projection_updated_at = max(
        (_timestamp_value(node.data) for node in visited_nodes.values()),
        default=None,
    )
    if projection_updated_at == "":
        projection_updated_at = None

    refs = _operator_trace_refs(
        trace_id=trace_id,
        base_refs=_build_refs_from_chains(upstream_chain, downstream_chain, graph),
        source_chain=source_chain,
        deployment_chain=deployment_chain,
        runtime_chain=runtime_chain,
        telemetry_events=telemetry_events,
        broker_order_lifecycle=broker_order_lifecycle,
        incident_refs=incident_refs,
        postmortem_refs=postmortem_refs,
        evolution_refs=evolution_refs,
        request_ids=request_ids,
        artifact_refs=artifact_refs,
    )

    return {
        "target_type": NODE_TRACE,
        "target_id": trace_id,
        "query_family": "source_runtime_telemetry_trace",
        "derived_only": True,
        "projection_updated_at": projection_updated_at,
        "upstream_chain": upstream_chain,
        "downstream_chain": downstream_chain,
        "conflict_markers": conflict_markers,
        "missing_edges": missing_edges,
        "operator_trace": {
            "source_chain": source_chain,
            "deployment_chain": deployment_chain,
            "runtime_chain": runtime_chain,
            "broker_order_lifecycle": broker_order_lifecycle,
            "telemetry_events": telemetry_events,
            "incident_refs": incident_refs,
            "postmortem_refs": postmortem_refs,
            "evolution_refs": evolution_refs,
        },
        "refs": refs,
        "telemetry_event_count": len(telemetry_events),
        "broker_order_event_count": len(broker_order_lifecycle),
        "evolution_decision_count": len(evolution_refs),
        "_meta": {
            "visited_node_count": len(visited_nodes),
            "visited_edge_count": len(upstream_chain) + len(downstream_chain),
        },
    }


def _append_research_chain_for_artifact(
    graph: LineageGraph,
    artifact_node: GraphNode,
    chain: list[dict[str, Any]],
    missing_edges: list[dict[str, Any]],
    conflict_markers: list[dict[str, Any]],
    remember,
) -> None:
    artifact_data = artifact_node.data
    run_id = artifact_data.get("run_id") or artifact_data.get("experiment_run_id")
    experiment_node = remember(_known_or_missing(
        graph,
        missing_edges,
        conflict_markers,
        edge_type=EDGE_ARTIFACT_EXPERIMENT,
        from_type=NODE_CANDIDATE_ARTIFACT,
        from_id=artifact_node.node_id,
        to_type=NODE_EXPERIMENT_RUN,
        to_id=str(run_id) if run_id else None,
    ))
    if experiment_node is not None:
        _append_research_chain_for_experiment(
            graph,
            experiment_node,
            chain,
            missing_edges,
            conflict_markers,
            remember,
        )
    _append_unique(chain, _chain_item(artifact_node))
    if artifact_data.get("artifact_id") and artifact_data.get("artifact_version"):
        _append_unique(chain, {
            "type": NODE_ARTIFACT_REF,
            "id": _artifact_ref(str(artifact_data["artifact_id"]), str(artifact_data["artifact_version"])),
        })


def _append_research_chain_for_experiment(
    graph: LineageGraph,
    experiment_node: GraphNode,
    chain: list[dict[str, Any]],
    missing_edges: list[dict[str, Any]],
    conflict_markers: list[dict[str, Any]],
    remember,
) -> None:
    strategy_id = experiment_node.data.get("strategy_id") or experiment_node.data.get("strategy_spec_id")
    strategy_node = remember(_known_or_missing(
        graph,
        missing_edges,
        conflict_markers,
        edge_type=EDGE_EXPERIMENT_STRATEGY,
        from_type=NODE_EXPERIMENT_RUN,
        from_id=experiment_node.node_id,
        to_type=NODE_STRATEGY_SPEC,
        to_id=str(strategy_id) if strategy_id else None,
    ))
    if strategy_node is not None:
        _append_research_chain_for_strategy(
            graph,
            strategy_node,
            chain,
            missing_edges,
            conflict_markers,
            remember,
        )
    _append_unique(chain, _chain_item(experiment_node))


def _append_research_chain_for_strategy(
    graph: LineageGraph,
    strategy_node: GraphNode,
    chain: list[dict[str, Any]],
    missing_edges: list[dict[str, Any]],
    conflict_markers: list[dict[str, Any]],
    remember,
) -> None:
    source_id = strategy_node.data.get("source_id") or strategy_node.data.get("source_record_id")
    source_node = remember(_known_or_missing(
        graph,
        missing_edges,
        conflict_markers,
        edge_type=EDGE_STRATEGY_SPEC_SOURCE,
        from_type=NODE_STRATEGY_SPEC,
        from_id=strategy_node.node_id,
        to_type=NODE_SOURCE_RECORD,
        to_id=str(source_id) if source_id else None,
    ))
    if source_node is not None:
        _append_unique(chain, _chain_item(source_node))
    _append_unique(chain, _chain_item(strategy_node))


def _related_broker_order_nodes(
    graph: LineageGraph,
    *,
    trace_id: str,
    binding_ids: set[str],
    plan_ids: set[str],
    telemetry_event_ids: set[str],
) -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for node in graph.nodes_by_type(NODE_BROKER_ORDER_EVENT):
        data = node.data
        telemetry_id = data.get("telemetry_event_id") or data.get("event_id")
        binding_id = data.get("runtime_binding_id") or data.get("binding_id")
        plan_id = data.get("deployment_plan_id") or data.get("plan_id")
        if (
            data.get("trace_id") == trace_id
            or telemetry_id in telemetry_event_ids
            or binding_id in binding_ids
            or plan_id in plan_ids
        ):
            nodes.append(node)
    return sorted(nodes, key=_node_sort_key)


def _related_incident_nodes(
    graph: LineageGraph,
    *,
    trace_id: str,
    binding_ids: set[str],
    telemetry_event_ids: set[str],
) -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for node in graph.nodes_by_type(NODE_INCIDENT_CASE):
        data = node.data
        event_ids = {str(value) for value in data.get("telemetry_event_ids", []) if value}
        binding_id = data.get("runtime_binding_id") or data.get("binding_id")
        if (
            data.get("trace_id") == trace_id
            or binding_id in binding_ids
            or bool(event_ids & telemetry_event_ids)
        ):
            nodes.append(node)
    return sorted(nodes, key=_node_sort_key)


def _related_postmortem_nodes(
    graph: LineageGraph,
    incident_ids: set[str],
) -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for node in graph.nodes_by_type(NODE_POSTMORTEM):
        if node.data.get("incident_id") in incident_ids or node.data.get("linked_incident_id") in incident_ids:
            nodes.append(node)
    return sorted(nodes, key=_node_sort_key)


def _related_evolution_nodes(
    graph: LineageGraph,
    *,
    trace_id: str,
    artifact_ids: set[str],
    strategy_ids: set[str],
    pool_ids: set[str],
    persona_binding_ids: set[str],
    incident_ids: set[str],
    postmortem_ids: set[str],
    telemetry_event_ids: set[str],
    plan_ids: set[str],
    binding_ids: set[str],
) -> list[GraphNode]:
    known_refs = (
        {trace_id}
        | artifact_ids
        | strategy_ids
        | pool_ids
        | persona_binding_ids
        | incident_ids
        | postmortem_ids
        | telemetry_event_ids
        | plan_ids
        | binding_ids
    )
    nodes: list[GraphNode] = []
    for node in graph.nodes_by_type(NODE_EVOLUTION_DECISION):
        data = node.data
        evidence_ref_ids = {
            str(ref.get("ref_id"))
            for ref in data.get("evidence_refs", [])
            if isinstance(ref, dict) and ref.get("ref_id")
        }
        if (
            data.get("target_id") in known_refs
            or data.get("capital_pool_id") in pool_ids
            or data.get("linked_incident_id") in incident_ids
            or data.get("linked_postmortem_id") in postmortem_ids
            or bool(evidence_ref_ids & known_refs)
        ):
            nodes.append(node)
    return sorted(nodes, key=_node_sort_key)


def _operator_trace_refs(
    *,
    trace_id: str,
    base_refs: Optional[dict[str, Any]] = None,
    source_chain: Optional[list[dict[str, Any]]] = None,
    deployment_chain: Optional[list[dict[str, Any]]] = None,
    runtime_chain: Optional[list[dict[str, Any]]] = None,
    telemetry_events: Optional[list[dict[str, Any]]] = None,
    broker_order_lifecycle: Optional[list[dict[str, Any]]] = None,
    incident_refs: Optional[list[dict[str, Any]]] = None,
    postmortem_refs: Optional[list[dict[str, Any]]] = None,
    evolution_refs: Optional[list[dict[str, Any]]] = None,
    request_ids: Optional[set[str]] = None,
    artifact_refs: Optional[set[str]] = None,
) -> dict[str, Any]:
    refs = dict(base_refs or {
        "strategy_ids": [],
        "registry_ids": [],
        "runtime_binding_ids": [],
        "deployment_plan_ids": [],
        "capital_pool_ids": [],
        "persona_capital_binding_ids": [],
        "artifact_refs": [],
        "trace_ids": [],
    })
    buckets: dict[str, set[str]] = {
        key: {str(value) for value in refs.get(key, []) if value}
        for key in (
            "strategy_ids",
            "registry_ids",
            "runtime_binding_ids",
            "deployment_plan_ids",
            "capital_pool_ids",
            "persona_capital_binding_ids",
            "artifact_refs",
            "trace_ids",
        )
    }
    buckets.setdefault("trace_ids", set()).add(trace_id)
    if artifact_refs:
        buckets.setdefault("artifact_refs", set()).update(artifact_refs)

    extra: dict[str, set[str]] = {
        "source_record_ids": set(),
        "experiment_run_ids": set(),
        "approval_decision_ids": set(),
        "telemetry_event_ids": set(),
        "broker_order_event_ids": set(),
        "broker_order_ids": set(),
        "incident_ids": set(),
        "postmortem_ids": set(),
        "evolution_decision_ids": set(),
        "runtime_ids": set(),
        "request_ids": set(request_ids or set()),
    }

    all_items = (
        list(source_chain or [])
        + list(deployment_chain or [])
        + list(runtime_chain or [])
        + list(telemetry_events or [])
        + list(broker_order_lifecycle or [])
        + list(incident_refs or [])
        + list(postmortem_refs or [])
        + list(evolution_refs or [])
    )
    for item in all_items:
        item_type = item.get("type")
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if item_type == NODE_SOURCE_RECORD:
            extra["source_record_ids"].add(item_id)
        elif item_type == NODE_STRATEGY_SPEC:
            buckets.setdefault("strategy_ids", set()).add(item_id)
        elif item_type == NODE_EXPERIMENT_RUN:
            extra["experiment_run_ids"].add(item_id)
        elif item_type == NODE_CANDIDATE_ARTIFACT:
            if item.get("artifact_version"):
                buckets.setdefault("artifact_refs", set()).add(_artifact_ref(item_id, str(item["artifact_version"])))
        elif item_type == NODE_APPROVAL_DECISION:
            extra["approval_decision_ids"].add(item_id)
        elif item_type == NODE_DEPLOYMENT_PLAN:
            buckets.setdefault("deployment_plan_ids", set()).add(item_id)
        elif item_type == NODE_RUNTIME_BINDING:
            buckets.setdefault("runtime_binding_ids", set()).add(item_id)
        elif item_type == NODE_RUNTIME_REF:
            extra["runtime_ids"].add(item_id)
        elif item_type == NODE_TELEMETRY_EVENT:
            extra["telemetry_event_ids"].add(item_id)
        elif item_type == NODE_BROKER_ORDER_EVENT:
            extra["broker_order_event_ids"].add(item_id)
        elif item_type == NODE_INCIDENT_CASE:
            extra["incident_ids"].add(item_id)
        elif item_type == NODE_POSTMORTEM:
            extra["postmortem_ids"].add(item_id)
        elif item_type == NODE_EVOLUTION_DECISION:
            extra["evolution_decision_ids"].add(item_id)
        if item.get("order_id"):
            extra["broker_order_ids"].add(str(item["order_id"]))
        if item.get("runtime_id"):
            extra["runtime_ids"].add(str(item["runtime_id"]))
        if item.get("request_id"):
            extra["request_ids"].add(str(item["request_id"]))

    output = {key: sorted(values) for key, values in buckets.items()}
    output.update({key: sorted(values) for key, values in extra.items()})
    return output


# ---------------------------------------------------------------------------
# Summary envelope helpers (LIN-001 / L1 contract)
# ---------------------------------------------------------------------------

def _build_refs_from_chains(
    upstream_chain: list[dict[str, Any]],
    downstream_chain: list[dict[str, Any]],
    graph: LineageGraph,
    *,
    target_node: Optional[GraphNode] = None,
) -> dict[str, Any]:
    """
    Build the canonical refs envelope by scanning all chain items and their
    connected graph nodes.

    When ``target_node`` is supplied (e.g. the telemetry event for
    ``telemetry_event_trace``), its own semantic fields are also merged into
    the refs so that target-carried values like ``trace_id``, ``strategy_id``,
    and ``registry_id`` are never silently dropped.

    This produces the exact key set required by the LIN-001 summary projection
    contract:
      strategy_ids, registry_ids, runtime_binding_ids, deployment_plan_ids,
      capital_pool_ids, persona_capital_binding_ids, artifact_refs, trace_ids
    """
    strategy_ids: set[str] = set()
    registry_ids: set[str] = set()
    runtime_binding_ids: set[str] = set()
    deployment_plan_ids: set[str] = set()
    capital_pool_ids: set[str] = set()
    persona_capital_binding_ids: set[str] = set()
    artifact_refs_set: set[str] = set()
    trace_ids: set[str] = set()

    # -- Include target node's own semantic refs before scanning chain items --
    if target_node is not None:
        _merge_refs_from_node(
            target_node.node_type, target_node.node_id, target_node.data,
            graph,
            strategy_ids, registry_ids, runtime_binding_ids,
            deployment_plan_ids, capital_pool_ids,
            persona_capital_binding_ids, artifact_refs_set, trace_ids,
        )

    all_items = list(upstream_chain) + list(downstream_chain)

    for item in all_items:
        itype = item.get("type", "")
        iid = item.get("id", "")

        # Skip items already consumed via target_node (avoid double-counting
        # the same node when it also appears in the chain)
        if target_node and itype == target_node.node_type and iid == target_node.node_id:
            continue

        _merge_refs_from_node(
            itype, iid, None,
            graph,
            strategy_ids, registry_ids, runtime_binding_ids,
            deployment_plan_ids, capital_pool_ids,
            persona_capital_binding_ids, artifact_refs_set, trace_ids,
        )

    return {
        "strategy_ids": sorted(strategy_ids),
        "registry_ids": sorted(registry_ids),
        "runtime_binding_ids": sorted(runtime_binding_ids),
        "deployment_plan_ids": sorted(deployment_plan_ids),
        "capital_pool_ids": sorted(capital_pool_ids),
        "persona_capital_binding_ids": sorted(persona_capital_binding_ids),
        "artifact_refs": sorted(artifact_refs_set),
        "trace_ids": sorted(trace_ids),
    }


def _merge_refs_from_node(
    node_type: str,
    node_id: str,
    data: Optional[dict[str, Any]],
    graph: LineageGraph,
    strategy_ids: set[str],
    registry_ids: set[str],
    runtime_binding_ids: set[str],
    deployment_plan_ids: set[str],
    capital_pool_ids: set[str],
    persona_capital_binding_ids: set[str],
    artifact_refs_set: set[str],
    trace_ids: set[str],
) -> None:
    """Merge semantic refs from a single node into the accumulator sets."""
    if data is None:
        node = graph.get_node(node_type, node_id)
        if node is None:
            return
        data = node.data

    if node_type == NODE_RUNTIME_BINDING:
        runtime_binding_ids.add(node_id)
        if data.get("capital_pool_id"):
            capital_pool_ids.add(data["capital_pool_id"])
        if data.get("plan_id"):
            deployment_plan_ids.add(data["plan_id"])
        if data.get("persona_capital_binding_id"):
            persona_capital_binding_ids.add(data["persona_capital_binding_id"])
        if data.get("artifact_id") and data.get("artifact_version"):
            artifact_refs_set.add(_artifact_ref(data["artifact_id"], data["artifact_version"]))

    elif node_type == NODE_DEPLOYMENT_PLAN:
        deployment_plan_ids.add(node_id)
        if data.get("capital_pool_id"):
            capital_pool_ids.add(data["capital_pool_id"])
        if data.get("binding_id"):
            persona_capital_binding_ids.add(data["binding_id"])
        if data.get("artifact_id") and data.get("artifact_version"):
            artifact_refs_set.add(_artifact_ref(data["artifact_id"], data["artifact_version"]))

    elif node_type == NODE_CAPITAL_POOL:
        capital_pool_ids.add(node_id)

    elif node_type == NODE_PERSONA_BINDING:
        persona_capital_binding_ids.add(node_id)
        if data.get("capital_pool_id"):
            capital_pool_ids.add(data["capital_pool_id"])

    elif node_type == NODE_TELEMETRY_EVENT:
        if data.get("binding_id") or data.get("runtime_binding_id"):
            runtime_binding_ids.add(data.get("runtime_binding_id") or data.get("binding_id", ""))
        if data.get("plan_id") or data.get("deployment_plan_id"):
            deployment_plan_ids.add(data.get("deployment_plan_id") or data.get("plan_id", ""))
        if data.get("capital_pool_id"):
            capital_pool_ids.add(data["capital_pool_id"])
        if data.get("persona_capital_binding_id"):
            persona_capital_binding_ids.add(data["persona_capital_binding_id"])
        if data.get("artifact_id") and data.get("artifact_version"):
            artifact_refs_set.add(_artifact_ref(data["artifact_id"], data["artifact_version"]))
        if data.get("trace_id"):
            trace_ids.add(data["trace_id"])
        strategy_id = data.get("strategy_id") or _nested_value(data, "target", "strategy_id")
        if strategy_id:
            strategy_ids.add(str(strategy_id))
        registry_id = data.get("registry_id") or _nested_value(data, "target", "registry_id")
        if registry_id:
            registry_ids.add(str(registry_id))

    elif node_type == "artifact_ref":
        artifact_refs_set.add(node_id)

    elif node_type == "runtime_ref":
        for bnode in graph.nodes_by_type(NODE_RUNTIME_BINDING):
            if bnode.data.get("runtime_id") == node_id:
                runtime_binding_ids.add(bnode.node_id)


def _enrich_runtime_binding_projection(
    result: ProjectionResult,
    graph: LineageGraph,
) -> dict[str, Any]:
    """Enrich runtime binding projection with artifact_ref and runtime_ref."""
    binding_node = graph.get_node(NODE_RUNTIME_BINDING, result.target_id)
    if binding_node:
        artifact_id = binding_node.data.get("artifact_id", "")
        artifact_version = binding_node.data.get("artifact_version", "")
        runtime_id = binding_node.data.get("runtime_id", "")

        # Insert artifact_ref into upstream chain
        artifact_ref_item = {"type": "artifact_ref", "id": _artifact_ref(artifact_id, artifact_version)}
        result.upstream_chain.append(artifact_ref_item)

        # Insert runtime_ref into downstream chain
        if runtime_id:
            runtime_ref_item = {"type": "runtime_ref", "id": runtime_id}
            result.downstream_chain.insert(0, runtime_ref_item)

        # Add telemetry_event_count and binding_status from node data
        telemetry_events = [
            item for item in result.downstream_chain
            if item.get("type") == NODE_TELEMETRY_EVENT
        ]

    refs = _build_refs_from_chains(
        result.upstream_chain, result.downstream_chain, graph,
        target_node=binding_node,
    )

    return {
        "target_type": result.target_type,
        "target_id": result.target_id,
        "derived_only": True,
        "projection_updated_at": result.projection_updated_at,
        "upstream_chain": result.upstream_chain,
        "downstream_chain": result.downstream_chain,
        "conflict_markers": result.conflict_markers,
        "refs": refs,
        "telemetry_event_count": len([
            item for item in result.downstream_chain
            if item.get("type") == NODE_TELEMETRY_EVENT
        ]),
        "binding_status": binding_node.data.get("status") if binding_node else None,
        "_meta": {
            "visited_node_count": result.visited_node_count,
            "visited_edge_count": result.visited_edge_count,
        },
    }


def _enrich_capital_pool_projection(
    result: ProjectionResult,
    graph: LineageGraph,
) -> dict[str, Any]:
    """Enrich capital pool projection with runtime_ref and counts."""
    # The upstream chain for a pool starts with the pool itself
    result.upstream_chain.insert(0, {
        "type": NODE_CAPITAL_POOL,
        "id": result.target_id,
    })

    # Add runtime_ref nodes for each unique runtime_id in downstream bindings
    runtime_ids_seen: set[str] = set()
    for item in result.downstream_chain:
        if item.get("type") == NODE_RUNTIME_BINDING:
            binding_node = graph.get_node(NODE_RUNTIME_BINDING, item["id"])
            if binding_node:
                rid = binding_node.data.get("runtime_id")
                if rid and rid not in runtime_ids_seen:
                    runtime_ids_seen.add(rid)
                    result.downstream_chain.append({"type": "runtime_ref", "id": rid})

    # Count active runtime bindings
    active_count = 0
    for item in result.downstream_chain:
        if item.get("type") == NODE_RUNTIME_BINDING:
            binding_node = graph.get_node(NODE_RUNTIME_BINDING, item["id"])
            if binding_node and binding_node.data.get("status") == "active":
                active_count += 1

    # Check single_runtime_violation conflict
    pool_node = graph.get_node(NODE_CAPITAL_POOL, result.target_id)
    if pool_node and pool_node.data.get("single_runtime_enforced", True) and active_count > 1:
        active_binding_ids = []
        for item in result.downstream_chain:
            if item.get("type") == NODE_RUNTIME_BINDING:
                binding_node = graph.get_node(NODE_RUNTIME_BINDING, item["id"])
                if binding_node and binding_node.data.get("status") == "active":
                    active_binding_ids.append(item["id"])
        if active_binding_ids:
            result.conflict_markers.append({
                "code": "single_runtime_violation",
                "id": ",".join(active_binding_ids),
            })

    refs = _build_refs_from_chains(
        result.upstream_chain, result.downstream_chain, graph,
        target_node=pool_node,
    )

    return {
        "target_type": result.target_type,
        "target_id": result.target_id,
        "derived_only": True,
        "projection_updated_at": result.projection_updated_at,
        "upstream_chain": result.upstream_chain,
        "downstream_chain": result.downstream_chain,
        "conflict_markers": result.conflict_markers,
        "refs": refs,
        "telemetry_event_count": len([
            item for item in result.downstream_chain
            if item.get("type") == NODE_TELEMETRY_EVENT
        ]),
        "active_runtime_binding_count": active_count,
        "_meta": {
            "visited_node_count": result.visited_node_count,
            "visited_edge_count": result.visited_edge_count,
        },
    }


def _enrich_telemetry_event_trace(
    result: ProjectionResult,
    graph: LineageGraph,
) -> dict[str, Any]:
    """Enrich telemetry event trace with artifact_ref, runtime_ref, and alias conflict detection."""
    event_node = graph.get_node(NODE_TELEMETRY_EVENT, result.target_id)
    if event_node:
        artifact_id = event_node.data.get("artifact_id", "")
        artifact_version = event_node.data.get("artifact_version", "")
        runtime_id = event_node.data.get("runtime_id", "")

        # Insert artifact_ref into upstream chain
        artifact_ref_item = {"type": "artifact_ref", "id": _artifact_ref(artifact_id, artifact_version)}
        result.upstream_chain.append(artifact_ref_item)

        # Insert runtime_ref into downstream chain
        if runtime_id:
            result.downstream_chain.append({"type": "runtime_ref", "id": runtime_id})

    # Carry event_type through
    event_type = event_node.data.get("event_type") if event_node else None

    # Also detect alias conflicts for any telemetry events found in traversal chains
    for item in list(result.upstream_chain) + list(result.downstream_chain):
        if item.get("type") == NODE_TELEMETRY_EVENT:
            evt_node = graph.get_node(NODE_TELEMETRY_EVENT, item["id"])
            if evt_node and evt_node.node_id != result.target_id:
                d = evt_node.data
                rb_id = d.get("runtime_binding_id")
                binding_id = d.get("binding_id")
                if rb_id and binding_id and rb_id != binding_id:
                    result.conflict_markers.append({
                        "code": "runtime_binding_alias_mismatch",
                        "left": rb_id,
                        "right": binding_id,
                    })
                dp_id = d.get("deployment_plan_id")
                plan_id = d.get("plan_id")
                if dp_id and plan_id and dp_id != plan_id:
                    result.conflict_markers.append({
                        "code": "deployment_plan_alias_mismatch",
                        "left": dp_id,
                        "right": plan_id,
                    })
                stage = d.get("deployment_stage")
                env = d.get("environment")
                if stage and env and stage != env:
                    result.conflict_markers.append({
                        "code": "deployment_stage_alias_mismatch",
                        "left": stage,
                        "right": env,
                    })
                top_level_av = d.get("artifact_version")
                target_av = d.get("target", {}).get("artifact_version") if isinstance(d.get("target"), dict) else None
                if top_level_av and target_av and top_level_av != target_av:
                    result.conflict_markers.append({
                        "code": "artifact_version_target_mismatch",
                        "left": top_level_av,
                        "right": target_av,
                    })

    refs = _build_refs_from_chains(
        result.upstream_chain, result.downstream_chain, graph,
        target_node=event_node,
    )

    return {
        "target_type": result.target_type,
        "target_id": result.target_id,
        "derived_only": True,
        "projection_updated_at": result.projection_updated_at,
        "upstream_chain": result.upstream_chain,
        "downstream_chain": result.downstream_chain,
        "conflict_markers": result.conflict_markers,
        "refs": refs,
        "event_type": event_type,
        "_meta": {
            "visited_node_count": result.visited_node_count,
            "visited_edge_count": result.visited_edge_count,
        },
    }


def _enrich_forensic_plan_trace(
    result: ProjectionResult,
    graph: LineageGraph,
) -> dict[str, Any]:
    """Enrich forensic plan trace with artifact_ref, runtime_ref, and rollback metadata."""
    plan_node = graph.get_node(NODE_DEPLOYMENT_PLAN, result.target_id)
    if plan_node:
        artifact_id = plan_node.data.get("artifact_id", "")
        artifact_version = plan_node.data.get("artifact_version", "")

        # Insert plan itself into upstream chain
        result.upstream_chain.insert(0, {
            "type": NODE_DEPLOYMENT_PLAN,
            "id": result.target_id,
        })

        # Insert artifact_ref into upstream chain
        artifact_ref_item = {"type": "artifact_ref", "id": _artifact_ref(artifact_id, artifact_version)}
        result.upstream_chain.append(artifact_ref_item)

        # Add rollback conflict markers
        rollback = plan_node.data.get("rollback") or {}
        if rollback.get("action_type"):
            result.conflict_markers.append({
                "code": "rollback_action_type",
                "id": rollback["action_type"],
            })
        if rollback.get("target_artifact_id") and rollback.get("target_version"):
            result.conflict_markers.append({
                "code": "rollback_target_artifact",
                "id": _artifact_ref(rollback["target_artifact_id"], rollback["target_version"]),
            })

    # Collect rollback markers and runtime_refs from ALL runtime binding nodes
    # in both upstream and downstream chains (rollback edges may be traversed in either direction)
    runtime_ids_seen: set[str] = set()
    for item in list(result.upstream_chain) + list(result.downstream_chain):
        if item.get("type") == NODE_RUNTIME_BINDING:
            binding_node = graph.get_node(NODE_RUNTIME_BINDING, item["id"])
            if binding_node:
                # Add rollback_parent markers
                if binding_node.data.get("rollback_parent"):
                    rp = binding_node.data["rollback_parent"]
                    if not any(c.get("code") == "rollback_parent" and c.get("id") == rp for c in result.conflict_markers):
                        result.conflict_markers.append({
                            "code": "rollback_parent",
                            "id": rp,
                        })
                    # Also add the rollback target as a chain item if not already present
                    if not any(c.get("type") == NODE_RUNTIME_BINDING and c.get("id") == rp for c in result.upstream_chain + result.downstream_chain):
                        target_node = graph.get_node(NODE_RUNTIME_BINDING, rp)
                        if target_node:
                            result.downstream_chain.append({
                                "type": NODE_RUNTIME_BINDING,
                                "id": rp,
                                "status": target_node.data.get("status"),
                            })
                # Add rollback_action_type markers
                if binding_node.data.get("rollback_action_type"):
                    rat = binding_node.data["rollback_action_type"]
                    if not any(c.get("code") == "rollback_action_type" and c.get("id") == rat for c in result.conflict_markers):
                        result.conflict_markers.append({
                            "code": "rollback_action_type",
                            "id": rat,
                        })
                # Collect runtime_ref
                rid = binding_node.data.get("runtime_id")
                if rid and rid not in runtime_ids_seen:
                    runtime_ids_seen.add(rid)

    # Add runtime_ref nodes to downstream chain
    for rid in sorted(runtime_ids_seen):
        result.downstream_chain.append({"type": "runtime_ref", "id": rid})

    # Count runtime bindings and telemetry events in downstream chain
    rb_count = len([i for i in result.downstream_chain if i.get("type") == NODE_RUNTIME_BINDING])
    evt_count = len([i for i in result.downstream_chain if i.get("type") == NODE_TELEMETRY_EVENT])

    refs = _build_refs_from_chains(
        result.upstream_chain, result.downstream_chain, graph,
        target_node=plan_node,
    )

    return {
        "target_type": result.target_type,
        "target_id": result.target_id,
        "derived_only": True,
        "projection_updated_at": result.projection_updated_at,
        "upstream_chain": result.upstream_chain,
        "downstream_chain": result.downstream_chain,
        "conflict_markers": result.conflict_markers,
        "refs": refs,
        "runtime_binding_count": rb_count,
        "telemetry_event_count": evt_count,
        "_meta": {
            "visited_node_count": result.visited_node_count,
            "visited_edge_count": result.visited_edge_count,
        },
    }


# ---------------------------------------------------------------------------
# Corpus loader
# ---------------------------------------------------------------------------


class CorpusLoader:
    """Loads the LIN-001A benchmark corpus into a LineageGraph."""

    @staticmethod
    def load(corpus: dict[str, Any]) -> LineageGraph:
        graph = LineageGraph()
        node_sets = corpus.get("node_sets", {})

        # Load upstream research/source records
        for record in node_sets.get("source_records", []):
            source_id = _record_identifier(record, "source_id", "record_id", "id")
            if source_id:
                graph.add_node(NODE_SOURCE_RECORD, source_id, record)

        for record in node_sets.get("strategy_specs", []):
            strategy_id = _record_identifier(record, "strategy_id", "strategy_spec_id", "id")
            if not strategy_id:
                continue
            graph.add_node(NODE_STRATEGY_SPEC, strategy_id, record)
            source_id = record.get("source_id") or record.get("source_record_id")
            if source_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_STRATEGY_SPEC_SOURCE,
                    from_type=NODE_STRATEGY_SPEC,
                    from_id=strategy_id,
                    to_type=NODE_SOURCE_RECORD,
                    to_id=str(source_id),
                ))

        for record in node_sets.get("experiment_runs", []):
            run_id = _record_identifier(record, "run_id", "experiment_id", "id")
            if not run_id:
                continue
            graph.add_node(NODE_EXPERIMENT_RUN, run_id, record)
            strategy_id = record.get("strategy_id") or record.get("strategy_spec_id")
            if strategy_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_EXPERIMENT_STRATEGY,
                    from_type=NODE_EXPERIMENT_RUN,
                    from_id=run_id,
                    to_type=NODE_STRATEGY_SPEC,
                    to_id=str(strategy_id),
                ))

        for artifact_set in ("candidate_artifacts", "allocation_policy_artifacts", "research_artifacts"):
            for record in node_sets.get(artifact_set, []):
                artifact_id = _record_identifier(record, "artifact_id", "registry_id", "id")
                if not artifact_id:
                    continue
                graph.add_node(NODE_CANDIDATE_ARTIFACT, artifact_id, record)
                run_id = record.get("run_id") or record.get("experiment_run_id") or record.get("experiment_id")
                if run_id:
                    graph.add_edge(GraphEdge(
                        edge_type=EDGE_ARTIFACT_EXPERIMENT,
                        from_type=NODE_CANDIDATE_ARTIFACT,
                        from_id=artifact_id,
                        to_type=NODE_EXPERIMENT_RUN,
                        to_id=str(run_id),
                    ))

        for record in node_sets.get("approval_decisions", []):
            decision_id = _record_identifier(record, "decision_id", "approval_decision_id", "id")
            if not decision_id:
                continue
            graph.add_node(NODE_APPROVAL_DECISION, decision_id, record)
            target_id = record.get("target_id") or record.get("registry_target_id") or record.get("artifact_id")
            if target_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_APPROVAL_TARGET,
                    from_type=NODE_APPROVAL_DECISION,
                    from_id=decision_id,
                    to_type=NODE_CANDIDATE_ARTIFACT,
                    to_id=str(target_id),
                ))

        # Load capital pools
        for record in node_sets.get("capital_pools", []):
            graph.add_node(NODE_CAPITAL_POOL, record["pool_id"], record)

        # Load persona-capital bindings
        for record in node_sets.get("persona_capital_bindings", []):
            graph.add_node(NODE_PERSONA_BINDING, record["binding_id"], record)
            # Edge: persona_binding.capital_pool
            graph.add_edge(GraphEdge(
                edge_type="persona_binding.capital_pool",
                from_type=NODE_PERSONA_BINDING,
                from_id=record["binding_id"],
                to_type=NODE_CAPITAL_POOL,
                to_id=record["capital_pool_id"],
            ))

        # Load deployment plans
        for record in node_sets.get("deployment_plans", []):
            graph.add_node(NODE_DEPLOYMENT_PLAN, record["plan_id"], record)
            # Edge: deployment_plan.approval_decision
            if record.get("approval_decision_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_DEPLOYMENT_APPROVAL,
                    from_type=NODE_DEPLOYMENT_PLAN,
                    from_id=record["plan_id"],
                    to_type=NODE_APPROVAL_DECISION,
                    to_id=record["approval_decision_id"],
                ))
            # Edge: deployment_plan.artifact
            if record.get("artifact_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_DEPLOYMENT_ARTIFACT,
                    from_type=NODE_DEPLOYMENT_PLAN,
                    from_id=record["plan_id"],
                    to_type=NODE_CANDIDATE_ARTIFACT,
                    to_id=record["artifact_id"],
                ))
            # Edge: deployment_plan.capital_pool
            graph.add_edge(GraphEdge(
                edge_type=EDGE_DEPLOYMENT_POOL,
                from_type=NODE_DEPLOYMENT_PLAN,
                from_id=record["plan_id"],
                to_type=NODE_CAPITAL_POOL,
                to_id=record["capital_pool_id"],
            ))
            # Edge: deployment_plan.persona_binding
            graph.add_edge(GraphEdge(
                edge_type=EDGE_DEPLOYMENT_PERSONA,
                from_type=NODE_DEPLOYMENT_PLAN,
                from_id=record["plan_id"],
                to_type=NODE_PERSONA_BINDING,
                to_id=record["binding_id"],
            ))

        # Load runtime bindings
        for record in node_sets.get("runtime_bindings", []):
            graph.add_node(NODE_RUNTIME_BINDING, record["binding_id"], record)
            # Edge: runtime_binding.artifact
            if record.get("artifact_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_RUNTIME_ARTIFACT,
                    from_type=NODE_RUNTIME_BINDING,
                    from_id=record["binding_id"],
                    to_type=NODE_CANDIDATE_ARTIFACT,
                    to_id=record["artifact_id"],
                ))
            # Edge: runtime_binding.capital_pool
            graph.add_edge(GraphEdge(
                edge_type=EDGE_RUNTIME_POOL,
                from_type=NODE_RUNTIME_BINDING,
                from_id=record["binding_id"],
                to_type=NODE_CAPITAL_POOL,
                to_id=record["capital_pool_id"],
            ))
            # Edge: runtime_binding.deployment_plan
            graph.add_edge(GraphEdge(
                edge_type=EDGE_RUNTIME_PLAN,
                from_type=NODE_RUNTIME_BINDING,
                from_id=record["binding_id"],
                to_type=NODE_DEPLOYMENT_PLAN,
                to_id=record["plan_id"],
            ))
            # Edge: runtime_binding.persona_binding
            graph.add_edge(GraphEdge(
                edge_type=EDGE_RUNTIME_PERSONA,
                from_type=NODE_RUNTIME_BINDING,
                from_id=record["binding_id"],
                to_type=NODE_PERSONA_BINDING,
                to_id=record["persona_capital_binding_id"],
            ))
            # Edge: runtime_binding.rollback_parent (self-lineage)
            if record.get("rollback_parent"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_RUNTIME_ROLLBACK,
                    from_type=NODE_RUNTIME_BINDING,
                    from_id=record["binding_id"],
                    to_type=NODE_RUNTIME_BINDING,
                    to_id=record["rollback_parent"],
                ))

        # Load telemetry events
        for record in node_sets.get("telemetry_events", []):
            graph.add_node(NODE_TELEMETRY_EVENT, record["event_id"], record)
            binding_id = record.get("runtime_binding_id") or record.get("binding_id")
            if binding_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_TELEMETRY_BINDING,
                    from_type=NODE_TELEMETRY_EVENT,
                    from_id=record["event_id"],
                    to_type=NODE_RUNTIME_BINDING,
                    to_id=binding_id,
                ))
            plan_id = record.get("deployment_plan_id") or record.get("plan_id")
            if plan_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_TELEMETRY_PLAN,
                    from_type=NODE_TELEMETRY_EVENT,
                    from_id=record["event_id"],
                    to_type=NODE_DEPLOYMENT_PLAN,
                    to_id=plan_id,
                ))
            if record.get("capital_pool_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_TELEMETRY_POOL,
                    from_type=NODE_TELEMETRY_EVENT,
                    from_id=record["event_id"],
                    to_type=NODE_CAPITAL_POOL,
                    to_id=record["capital_pool_id"],
                ))
            if record.get("persona_capital_binding_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_TELEMETRY_PERSONA,
                    from_type=NODE_TELEMETRY_EVENT,
                    from_id=record["event_id"],
                    to_type=NODE_PERSONA_BINDING,
                    to_id=record["persona_capital_binding_id"],
                ))

        # Load broker/order lifecycle refs when a reconciliation slice supplies
        # owned records. Telemetry-derived lifecycle events still work without
        # this optional node set.
        for record in node_sets.get("broker_order_events", []):
            order_event_id = _record_identifier(record, "order_event_id", "event_id", "id", "order_id")
            if not order_event_id:
                continue
            graph.add_node(NODE_BROKER_ORDER_EVENT, order_event_id, record)
            binding_id = record.get("runtime_binding_id") or record.get("binding_id")
            if binding_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_BROKER_ORDER_BINDING,
                    from_type=NODE_BROKER_ORDER_EVENT,
                    from_id=order_event_id,
                    to_type=NODE_RUNTIME_BINDING,
                    to_id=str(binding_id),
                ))
            plan_id = record.get("deployment_plan_id") or record.get("plan_id")
            if plan_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_BROKER_ORDER_PLAN,
                    from_type=NODE_BROKER_ORDER_EVENT,
                    from_id=order_event_id,
                    to_type=NODE_DEPLOYMENT_PLAN,
                    to_id=str(plan_id),
                ))
            telemetry_event_id = record.get("telemetry_event_id")
            if telemetry_event_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_BROKER_ORDER_TELEMETRY,
                    from_type=NODE_BROKER_ORDER_EVENT,
                    from_id=order_event_id,
                    to_type=NODE_TELEMETRY_EVENT,
                    to_id=str(telemetry_event_id),
                ))

        for record in [*node_sets.get("incident_cases", []), *node_sets.get("incidents", [])]:
            incident_id = _record_identifier(record, "incident_id", "id")
            if not incident_id:
                continue
            graph.add_node(NODE_INCIDENT_CASE, incident_id, record)
            binding_id = record.get("runtime_binding_id") or record.get("binding_id")
            if binding_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_INCIDENT_BINDING,
                    from_type=NODE_INCIDENT_CASE,
                    from_id=incident_id,
                    to_type=NODE_RUNTIME_BINDING,
                    to_id=str(binding_id),
                ))
            for event_id in record.get("telemetry_event_ids", []) or []:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_INCIDENT_TELEMETRY,
                    from_type=NODE_INCIDENT_CASE,
                    from_id=incident_id,
                    to_type=NODE_TELEMETRY_EVENT,
                    to_id=str(event_id),
                ))

        for record in node_sets.get("postmortems", []):
            postmortem_id = _record_identifier(record, "postmortem_id", "id")
            if not postmortem_id:
                continue
            graph.add_node(NODE_POSTMORTEM, postmortem_id, record)
            incident_id = record.get("incident_id") or record.get("linked_incident_id")
            if incident_id:
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_POSTMORTEM_INCIDENT,
                    from_type=NODE_POSTMORTEM,
                    from_id=postmortem_id,
                    to_type=NODE_INCIDENT_CASE,
                    to_id=str(incident_id),
                ))

        for record in node_sets.get("evolution_decisions", []):
            decision_id = _record_identifier(record, "decision_id", "id")
            if not decision_id:
                continue
            graph.add_node(NODE_EVOLUTION_DECISION, decision_id, record)
            if record.get("linked_postmortem_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_EVOLUTION_POSTMORTEM,
                    from_type=NODE_EVOLUTION_DECISION,
                    from_id=decision_id,
                    to_type=NODE_POSTMORTEM,
                    to_id=str(record["linked_postmortem_id"]),
                ))
            if record.get("linked_incident_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_EVOLUTION_INCIDENT,
                    from_type=NODE_EVOLUTION_DECISION,
                    from_id=decision_id,
                    to_type=NODE_INCIDENT_CASE,
                    to_id=str(record["linked_incident_id"]),
                ))
            if record.get("target_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_EVOLUTION_TARGET,
                    from_type=NODE_EVOLUTION_DECISION,
                    from_id=decision_id,
                    to_type=NODE_CANDIDATE_ARTIFACT,
                    to_id=str(record["target_id"]),
                ))

        return graph


# ---------------------------------------------------------------------------
# Main service facade
# ---------------------------------------------------------------------------


class LineageReadService:
    """
    LIN-002 Lineage Read Service.

    High-level API that combines the graph, traverser, and projection builder
    into a single query interface.

    Usage:
        service = LineageReadService()
        service.load_corpus(corpus_dict)
        result = service.query("runtime_binding_projection", binding_id="rb-alpha-live-001")
    """

    def __init__(self) -> None:
        self.graph = LineageGraph()
        self.traverser = LineageTraverser(self.graph)
        self.projection = ProjectionBuilder()

    def load_corpus(self, corpus: dict[str, Any]) -> None:
        """Load a LIN-001A benchmark corpus into the service."""
        self.graph = CorpusLoader.load(corpus)
        self.traverser = LineageTraverser(self.graph)
        self.projection = ProjectionBuilder()

    def query(
        self,
        query_family: str,
        *,
        binding_id: Optional[str] = None,
        pool_id: Optional[str] = None,
        event_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Execute a lineage query by family name.

        Parameters
        ----------
        query_family : str
            One of: runtime_binding_projection, capital_pool_projection,
            telemetry_event_trace, forensic_plan_trace,
            source_runtime_telemetry_trace.
        binding_id : str, optional
            Runtime binding ID (for runtime_binding_projection).
        pool_id : str, optional
            Capital pool ID (for capital_pool_projection).
        event_id : str, optional
            Telemetry event ID (for telemetry_event_trace).
        plan_id : str, optional
            Deployment plan ID (for forensic_plan_trace).
        trace_id : str, optional
            Distributed trace ID (for source_runtime_telemetry_trace).

        Returns
        -------
        dict
            Projection payload matching the LIN-001A corpus shape.
        """
        if query_family == "runtime_binding_projection":
            if not binding_id:
                raise ValueError("binding_id required for runtime_binding_projection")
            return self.projection.runtime_binding_projection(self.traverser, binding_id)
        if query_family == "capital_pool_projection":
            if not pool_id:
                raise ValueError("pool_id required for capital_pool_projection")
            return self.projection.capital_pool_projection(self.traverser, pool_id)
        if query_family == "telemetry_event_trace":
            if not event_id:
                raise ValueError("event_id required for telemetry_event_trace")
            return self.projection.telemetry_event_trace(self.traverser, event_id)
        if query_family == "forensic_plan_trace":
            if not plan_id:
                raise ValueError("plan_id required for forensic_plan_trace")
            return self.projection.forensic_plan_trace(self.traverser, plan_id)
        if query_family == "source_runtime_telemetry_trace":
            if not trace_id:
                raise ValueError("trace_id required for source_runtime_telemetry_trace")
            return self.projection.source_runtime_telemetry_trace(self.traverser, trace_id)

        raise ValueError(f"Unknown query family: {query_family}")
