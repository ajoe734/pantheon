#!/usr/bin/env python3
"""Pantheon Admin CLI (skeleton)

Implements a minimal command surface per APP-002 Secondary Control Path spec.
This is a scaffold: each command currently prints intent and returns appropriate exit codes.
"""
import argparse
import sys

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_AUTH = 2
EXIT_USAGE = 3
EXIT_UNAVAILABLE = 4
EXIT_PARTIAL = 5


def cmd_deployment(args):
    print(f"[dry-run={args.dry_run}] Deployment {args.action} {args.plan_id} --reason '{args.reason}'")
    if args.action == "approve":
        print(f"Approval decision ID: ad-placeholder-{args.plan_id}")
    elif args.action == "reject":
        print("Rejected")
    return EXIT_SUCCESS


def cmd_runtime(args):
    action = args.action
    if action == "pause":
        print(f"Runtime pause {args.binding_id} duration={args.duration} reason='{args.reason}'")
    elif action == "resume":
        print(f"Runtime resume {args.binding_id}")
    elif action == "force-halt":
        print(f"Runtime force-halt {args.binding_id} confirm={args.confirm}")
    return EXIT_SUCCESS


def cmd_rollback(args):
    if args.action == "execute":
        print(f"Rollback execute target={args.target_id} to {args.rollback_to_version} (verify={args.verify_before_executing})")
    elif args.action == "list":
        print(f"Rollback list for {args.target_id}: []")
    elif args.action == "abort":
        print(f"Rollback abort {args.rollback_id}")
    return EXIT_SUCCESS


def cmd_kill_switch(args):
    if args.action == "activate":
        print(f"Kill-switch activate scope={args.scope} scope_id={args.scope_id} severity={args.severity} rationale={args.rationale}")
    elif args.action == "deactivate":
        print(f"Kill-switch deactivate scope={args.scope} scope_id={args.scope_id} rationale={args.rationale}")
    elif args.action == "status":
        print("Kill-switch status: none active")
    return EXIT_SUCCESS


def cmd_evolution(args):
    print(f"Evolution {args.action} {getattr(args, 'decision_id', None)}")
    return EXIT_SUCCESS


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pantheon-admin", description="Pantheon Admin CLI (skeleton)")
    parser.add_argument("--version", action="version", version="Pantheon Admin CLI v0.1.0")
    subparsers = parser.add_subparsers(dest="cmd")

    # deployment
    p_dep = subparsers.add_parser("deployment")
    dep_sub = p_dep.add_subparsers(dest="action")
    p_approve = dep_sub.add_parser("approve")
    p_approve.add_argument("plan_id")
    p_approve.add_argument("--reason", default="")
    p_approve.add_argument("--verification-timestamp", default="")
    p_approve.add_argument("--mfa-token", default=None)
    p_approve.add_argument("--dry-run", action="store_true")

    p_reject = dep_sub.add_parser("reject")
    p_reject.add_argument("plan_id")
    p_reject.add_argument("--reason", default="")
    p_reject.add_argument("--mfa-token", default=None)
    p_reject.add_argument("--dry-run", action="store_true")

    # runtime
    p_run = subparsers.add_parser("runtime")
    run_sub = p_run.add_subparsers(dest="action")
    p_pause = run_sub.add_parser("pause")
    p_pause.add_argument("binding_id")
    p_pause.add_argument("--reason", default="")
    p_pause.add_argument("--duration", type=int, default=3600)
    p_pause.add_argument("--mfa-token", default=None)
    p_pause.add_argument("--dry-run", action="store_true")

    p_resume = run_sub.add_parser("resume")
    p_resume.add_argument("binding_id")
    p_resume.add_argument("--mfa-token", default=None)
    p_resume.add_argument("--dry-run", action="store_true")

    p_force = run_sub.add_parser("force-halt")
    p_force.add_argument("binding_id")
    p_force.add_argument("--reason", default="")
    p_force.add_argument("--confirm", action="store_true")
    p_force.add_argument("--mfa-token", default=None)
    p_force.add_argument("--dry-run", action="store_true")

    # rollback
    p_rb = subparsers.add_parser("rollback")
    rb_sub = p_rb.add_subparsers(dest="action")
    p_rb_exec = rb_sub.add_parser("execute")
    p_rb_exec.add_argument("target_id")
    p_rb_exec.add_argument("--target-type", choices=["deployment","runtime"], required=True)
    p_rb_exec.add_argument("--rollback-to-version", required=True)
    p_rb_exec.add_argument("--reason", default="")
    p_rb_exec.add_argument("--verify-before-executing", action="store_true")
    p_rb_exec.add_argument("--mfa-token", default=None)

    p_rb_list = rb_sub.add_parser("list")
    p_rb_list.add_argument("target_id")

    p_rb_abort = rb_sub.add_parser("abort")
    p_rb_abort.add_argument("rollback_id")
    p_rb_abort.add_argument("--reason", default="")

    # kill-switch
    p_ks = subparsers.add_parser("kill-switch")
    ks_sub = p_ks.add_subparsers(dest="action")
    p_ks_act = ks_sub.add_parser("activate")
    p_ks_act.add_argument("--scope", choices=["all","persona","pool"], default="all")
    p_ks_act.add_argument("--scope-id", default=None)
    p_ks_act.add_argument("--severity", choices=["critical","high","medium"], default="critical")
    p_ks_act.add_argument("--rationale", default="")
    p_ks_act.add_argument("--force", action="store_true")
    p_ks_act.add_argument("--mfa-token", default=None)

    p_ks_status = ks_sub.add_parser("status")
    p_ks_status.add_argument("--scope", choices=["all","persona","pool"], default=None)
    p_ks_status.add_argument("--scope-id", default=None)

    p_ks_deact = ks_sub.add_parser("deactivate")
    p_ks_deact.add_argument("--scope", choices=["all","persona","pool"], required=True)
    p_ks_deact.add_argument("--scope-id", default=None)
    p_ks_deact.add_argument("--rationale", default="")
    p_ks_deact.add_argument("--mfa-token", default=None)

    # evolution
    p_ev = subparsers.add_parser("evolution")
    ev_sub = p_ev.add_subparsers(dest="action")
    p_ev_approve = ev_sub.add_parser("approve")
    p_ev_approve.add_argument("decision_id")
    p_ev_approve.add_argument("--mfa-token", default=None)

    p_ev_reject = ev_sub.add_parser("reject")
    p_ev_reject.add_argument("decision_id")
    p_ev_reject.add_argument("--mfa-token", default=None)

    p_ev_exec = ev_sub.add_parser("execute")
    p_ev_exec.add_argument("decision_id")
    p_ev_exec.add_argument("--action-type", choices=["freeze","retrain","mutate","retire"], required=True)
    p_ev_exec.add_argument("--action-params", default=None)
    p_ev_exec.add_argument("--mfa-token", default=None)

    args = parser.parse_args(argv[1:] if argv else None)

    if args.cmd == "deployment":
        return cmd_deployment(args)
    if args.cmd == "runtime":
        return cmd_runtime(args)
    if args.cmd == "rollback":
        return cmd_rollback(args)
    if args.cmd == "kill-switch":
        return cmd_kill_switch(args)
    if args.cmd == "evolution":
        return cmd_evolution(args)

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
