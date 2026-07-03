from __future__ import annotations

from pathlib import Path

import audit_management_frontend_tables as audit


def test_flags_management_table_without_dense_affordance(tmp_path: Path) -> None:
    source = tmp_path / "execute-plans" / "src" / "management"
    source.mkdir(parents=True)
    table = source / "BadTable.tsx"
    table.write_text(
        "export function BadTable() { return <div role=\"table\"><div>rows</div></div>; }\n",
        encoding="utf-8",
    )

    issues = audit.audit(tmp_path, ("execute-plans/src",))

    assert len(issues) == 1
    assert issues[0].category == "dense_table_affordance"
    assert issues[0].path == "execute-plans/src/management/BadTable.tsx"


def test_allows_management_table_with_sticky_scroll_affordance(tmp_path: Path) -> None:
    source = tmp_path / "execute-plans" / "src" / "management"
    source.mkdir(parents=True)
    table = source / "GoodTable.tsx"
    table.write_text(
        (
            "export function GoodTable() { return <div data-management-dense-table "
            "className=\"sticky-scrollbar\" role=\"table\"><div>rows</div></div>; }\n"
        ),
        encoding="utf-8",
    )

    assert audit.audit(tmp_path, ("execute-plans/src",)) == []
