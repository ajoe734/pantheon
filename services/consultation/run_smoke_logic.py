import sys
import tempfile
import uuid

try:
    from .models import ConsultMemo, ConsultRequest, ConsultRequestStatus, MemoStatus, utc_now
    from .store import ConsultationStore
except ImportError:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from services.consultation.models import (
        ConsultMemo,
        ConsultRequest,
        ConsultRequestStatus,
        MemoStatus,
        utc_now,
    )
    from services.consultation.store import ConsultationStore

def test_logic():
    with tempfile.TemporaryDirectory() as tmp_dir:
        print(f"Using temp dir: {tmp_dir}")
        store = ConsultationStore(tmp_dir)

        # 1. Create a request
        print("Testing: Create request")
        request_id = f"cr-{uuid.uuid4().hex[:12]}"
        new_req = ConsultRequest(
            request_id=request_id,
            request_type="strategy_review",
            requested_by={"actor_type": "persona", "actor_id": "p-alpha"},
            target_type="strategy",
            target_id="strat-001",
            trace_id="trace-123",
            status=ConsultRequestStatus.DRAFT,
            created_at=utc_now()
        )
        store.put_request(new_req)

        saved_req = store.get_request(request_id)
        assert saved_req.request_id == request_id
        assert saved_req.status == ConsultRequestStatus.DRAFT
        print(f"OK: Created request {request_id}")

        # 2. Submit the request
        print("Testing: Submit request")
        saved_req.status = ConsultRequestStatus.SUBMITTED
        store.put_request(saved_req)

        submitted_req = store.get_request(request_id)
        assert submitted_req.status == ConsultRequestStatus.SUBMITTED
        print("OK: Submitted request")

        # 3. List requests
        print("Testing: List requests")
        all_reqs = store.list_requests()
        assert len(all_reqs) >= 1
        print(f"OK: Listed {len(all_reqs)} requests")

        # 4. Submit a memo
        print("Testing: Submit memo")
        memo_id = f"mem-{uuid.uuid4().hex[:12]}"
        new_memo = ConsultMemo(
            memo_id=memo_id,
            request_id=request_id,
            target_type="strategy",
            target_id="strat-001",
            memo_type="committee_summary",
            author_type="persona",
            author_ref="p-risk-analyst",
            summary="This strategy looks good but has some tail risk.",
            findings=[
                {
                    "severity": "medium",
                    "category": "risk",
                    "claim": "Tail risk is higher than reported",
                    "recommendation": "Lower capital allocation"
                }
            ],
            recommendation="approve_with_conditions",
            status=MemoStatus.DRAFT,
            created_at=utc_now(),
            trace_id="trace-123"
        )
        store.put_memo(new_memo)

        saved_memo = store.get_memo(memo_id)
        assert saved_memo.memo_id == memo_id
        print(f"OK: Created memo {memo_id}")

        # 5. Publish the memo
        print("Testing: Publish memo")
        saved_memo.status = MemoStatus.PUBLISHED
        saved_memo.published_at = utc_now()
        store.put_memo(saved_memo)

        published_memo = store.get_memo(memo_id)
        assert published_memo.status == MemoStatus.PUBLISHED
        assert published_memo.published_at is not None
        print("OK: Published memo")

        # 6. Get memos for target
        print("Testing: Get memos for target")
        target_memos = store.list_memos_for_target("strategy", "strat-001")
        assert len(target_memos) >= 1
        assert any(m.memo_id == memo_id for m in target_memos)
        print("OK: Found memo for target")

        print("\nALL LOGIC TESTS PASSED")

if __name__ == "__main__":
    try:
        test_logic()
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
