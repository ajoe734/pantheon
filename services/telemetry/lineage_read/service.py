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
EDGE_POSTMORTEM_INCIDENT = "postmortem.incident_case"
EDGE_EVOLUTION_POSTMORTEM = "evolution_decision.postmortem"

# Node type constants
NODE_CAPITAL_POOL = "capital_pool"
NODE_PERSONA_BINDING = "persona_capital_binding"
NODE_DEPLOYMENT_PLAN = "deployment_plan"
NODE_RUNTIME_BINDING = "runtime_binding"
NODE_TELEMETRY_EVENT = "telemetry_event"
NODE_ARTIFACT_REF = "artifact_ref"
NODE_RUNTIME_REF = "runtime_ref"

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


def _artifact_ref(artifact_id: str, artifact_version: str) -> str:
    return f"{artifact_id}@{artifact_version}"


# ---------------------------------------------------------------------------
# Summary envelope helpers (LIN-001 / L1 contract)
# ---------------------------------------------------------------------------

def _build_refs_from_chains(
    upstream_chain: list[dict[str, Any]],
    downstream_chain: list[dict[str, Any]],
    graph: LineageGraph,
) -> dict[str, Any]:
    """
    Build the canonical refs envelope by scanning all chain items and their
    connected graph nodes.

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

    all_items = list(upstream_chain) + list(downstream_chain)

    for item in all_items:
        itype = item.get("type", "")
        iid = item.get("id", "")

        if itype == NODE_RUNTIME_BINDING:
            runtime_binding_ids.add(iid)
            bnode = graph.get_node(NODE_RUNTIME_BINDING, iid)
            if bnode:
                d = bnode.data
                if d.get("capital_pool_id"):
                    capital_pool_ids.add(d["capital_pool_id"])
                if d.get("plan_id"):
                    deployment_plan_ids.add(d["plan_id"])
                if d.get("persona_capital_binding_id"):
                    persona_capital_binding_ids.add(d["persona_capital_binding_id"])
                if d.get("artifact_id") and d.get("artifact_version"):
                    artifact_refs_set.add(_artifact_ref(d["artifact_id"], d["artifact_version"]))

        elif itype == NODE_DEPLOYMENT_PLAN:
            deployment_plan_ids.add(iid)
            pnode = graph.get_node(NODE_DEPLOYMENT_PLAN, iid)
            if pnode:
                d = pnode.data
                if d.get("capital_pool_id"):
                    capital_pool_ids.add(d["capital_pool_id"])
                if d.get("binding_id"):
                    persona_capital_binding_ids.add(d["binding_id"])
                if d.get("artifact_id") and d.get("artifact_version"):
                    artifact_refs_set.add(_artifact_ref(d["artifact_id"], d["artifact_version"]))

        elif itype == NODE_CAPITAL_POOL:
            capital_pool_ids.add(iid)

        elif itype == NODE_PERSONA_BINDING:
            persona_capital_binding_ids.add(iid)
            pbnode = graph.get_node(NODE_PERSONA_BINDING, iid)
            if pbnode and pbnode.data.get("capital_pool_id"):
                capital_pool_ids.add(pbnode.data["capital_pool_id"])

        elif itype == NODE_TELEMETRY_EVENT:
            enode = graph.get_node(NODE_TELEMETRY_EVENT, iid)
            if enode:
                d = enode.data
                if d.get("binding_id") or d.get("runtime_binding_id"):
                    runtime_binding_ids.add(d.get("runtime_binding_id") or d.get("binding_id", ""))
                if d.get("plan_id") or d.get("deployment_plan_id"):
                    deployment_plan_ids.add(d.get("deployment_plan_id") or d.get("plan_id", ""))
                if d.get("capital_pool_id"):
                    capital_pool_ids.add(d["capital_pool_id"])
                if d.get("persona_capital_binding_id"):
                    persona_capital_binding_ids.add(d["persona_capital_binding_id"])
                if d.get("artifact_id") and d.get("artifact_version"):
                    artifact_refs_set.add(_artifact_ref(d["artifact_id"], d["artifact_version"]))
                if d.get("trace_id"):
                    trace_ids.add(d["trace_id"])
                if d.get("strategy_id"):
                    strategy_ids.add(d["strategy_id"])
                if d.get("registry_id"):
                    registry_ids.add(d["registry_id"])

        elif itype == "artifact_ref":
            artifact_refs_set.add(iid)

        elif itype == "runtime_ref":
            # Resolve runtime_ref back to binding(s)
            for bnode in graph.nodes_by_type(NODE_RUNTIME_BINDING):
                if bnode.data.get("runtime_id") == iid:
                    runtime_binding_ids.add(bnode.node_id)

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

    refs = _build_refs_from_chains(result.upstream_chain, result.downstream_chain, graph)

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

    refs = _build_refs_from_chains(result.upstream_chain, result.downstream_chain, graph)

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

    refs = _build_refs_from_chains(result.upstream_chain, result.downstream_chain, graph)

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

    refs = _build_refs_from_chains(result.upstream_chain, result.downstream_chain, graph)

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
            if record.get("plan_id"):
                graph.add_edge(GraphEdge(
                    edge_type=EDGE_TELEMETRY_PLAN,
                    from_type=NODE_TELEMETRY_EVENT,
                    from_id=record["event_id"],
                    to_type=NODE_DEPLOYMENT_PLAN,
                    to_id=record["plan_id"],
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
    ) -> dict[str, Any]:
        """
        Execute a lineage query by family name.

        Parameters
        ----------
        query_family : str
            One of: runtime_binding_projection, capital_pool_projection,
            telemetry_event_trace, forensic_plan_trace.
        binding_id : str, optional
            Runtime binding ID (for runtime_binding_projection).
        pool_id : str, optional
            Capital pool ID (for capital_pool_projection).
        event_id : str, optional
            Telemetry event ID (for telemetry_event_trace).
        plan_id : str, optional
            Deployment plan ID (for forensic_plan_trace).

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

        raise ValueError(f"Unknown query family: {query_family}")
