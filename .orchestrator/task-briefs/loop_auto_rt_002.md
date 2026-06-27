# Task Brief: LOOP-AUTO-RT-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement managed paper runtime fleet reconciler
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: all 3 ACs covered by named unit tests (34/34 pass). Fleet reconciler correctly wired to LOOP-AUTO-RT-001 desired-state endpoint. Fetch failure safety, SIGKILL restart logic, and excluded binding stop path are all correct. Return to Claude2 for closeout.

## Summary
實作 active paper RuntimeBinding 到 exactly-one supervised worker 的 fleet reconciler。
