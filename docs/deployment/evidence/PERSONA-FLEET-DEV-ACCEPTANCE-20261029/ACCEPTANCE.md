# PERSONA-FLEET-DEV-ACCEPTANCE-20261029

Status: release DONE, hosted browser acceptance NOT done (blocked).

- Pair served on dev: BFF `528c4fa232fef3b4ba11972dc0316c9f88c2090e` + FE `7f5cdba28727735700f6af80f954a582c7eef3c1`.
- Release: Pantheon Nonprod Deploy run 40887021529 (success, 13:59Z-14:58Z); admit-and-switch job 123506338191 success.
- Earlier run 40841011879 failed (exit 75 in lease job); superseded, root cause not investigated further.
- Not run: login/logout, filters, traversal, deep links, Agora, timing targets, screenshots. See evidence.json.
- Blocker: admintest credential retrieval needs ssh; ssh refuses to start in this sandbox. No timing or pass claims are made.
