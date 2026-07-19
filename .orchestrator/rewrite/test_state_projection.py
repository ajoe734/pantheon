from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_projection as sp
from state_projection import ProjectionError


def _created(tid, **kw):
    return {"type": sp.EVENT_TASK_CREATED, "id": tid, **kw}


def _trans(tid, action):
    return {"type": sp.EVENT_TASK_TRANSITION, "id": tid, "action": action}


class ProjectBoardTests(unittest.TestCase):
    def test_create_projects_todo(self) -> None:
        board = sp.project_board([_created("T1", owner="alice")])
        self.assertEqual(board["T1"]["status"], "todo")
        self.assertEqual(board["T1"]["owner"], "alice")

    def test_full_lifecycle(self) -> None:
        events = [
            _created("T1", owner="alice", reviewer="bob"),
            _trans("T1", "dispatch"),   # todo -> in_progress
            _trans("T1", "submit"),     # in_progress -> review
            _trans("T1", "approve"),    # review -> review_approved
            _trans("T1", "finalize"),   # review_approved -> done
        ]
        board = sp.project_board(events)
        self.assertEqual(board["T1"]["status"], "done")

    def test_reject_bumps_bounce_count(self) -> None:
        events = [
            _created("T1"),
            _trans("T1", "dispatch"),
            _trans("T1", "submit"),
            _trans("T1", "reject"),    # review -> in_progress, bounce++
            _trans("T1", "submit"),
            _trans("T1", "reject"),    # bounce++
        ]
        board = sp.project_board(events)
        self.assertEqual(board["T1"]["status"], "in_progress")
        self.assertEqual(board["T1"]["bounce_count"], 2)

    def test_illegal_transition_raises(self) -> None:
        with self.assertRaises(ProjectionError):
            sp.project_board([_created("T1"), _trans("T1", "approve")])  # todo can't approve

    def test_unknown_task_raises(self) -> None:
        with self.assertRaises(ProjectionError):
            sp.project_board([_trans("ghost", "dispatch")])

    def test_double_create_raises(self) -> None:
        with self.assertRaises(ProjectionError):
            sp.project_board([_created("T1"), _created("T1")])

    def test_owner_reviewer_changes(self) -> None:
        board = sp.project_board([
            _created("T1", owner="a", reviewer="b"),
            {"type": sp.EVENT_OWNER_CHANGED, "id": "T1", "owner": "c"},
            {"type": sp.EVENT_REVIEWER_CHANGED, "id": "T1", "reviewer": "d"},
        ])
        self.assertEqual(board["T1"]["owner"], "c")
        self.assertEqual(board["T1"]["reviewer"], "d")

    def test_next_is_appended_not_overwritten(self) -> None:
        board = sp.project_board([
            _created("T1"),
            {"type": sp.EVENT_NEXT_APPENDED, "id": "T1", "next": "first"},
            {"type": sp.EVENT_NEXT_APPENDED, "id": "T1", "next": "second"},
        ])
        # full history retained (the anti-pattern-K fix) — not a single overwrite
        self.assertEqual(board["T1"]["next_history"], ["first", "second"])
        self.assertEqual(sp.current_next(board["T1"]), "second")

    def test_truncation_yields_point_in_time_board(self) -> None:
        events = [
            _created("T1"),
            _trans("T1", "dispatch"),
            _trans("T1", "submit"),
        ]
        # projecting a prefix yields the board as of that point — the source-of-truth property
        self.assertEqual(sp.project_board(events[:2])["T1"]["status"], "in_progress")
        self.assertEqual(sp.project_board(events)["T1"]["status"], "review")

    def test_deterministic_replay(self) -> None:
        events = [_created("T1"), _trans("T1", "dispatch"), _created("T2")]
        self.assertEqual(sp.project_board(events), sp.project_board(events))


if __name__ == "__main__":
    unittest.main()
