#!/usr/bin/env python3
"""Read-only component probe; natural execution/browser proof belongs to the chain probe."""
import argparse
import json
import os
import urllib.parse
import urllib.request


def inspect_records(first, second):
    errors = []
    if not first.get('meeting_id') or first.get('meeting_id') == second.get('meeting_id'):
        errors.append('two distinct meeting identities required')
    if not first.get('tenant_id') or first.get('tenant_id') != second.get('tenant_id'):
        errors.append('same authenticated tenant required')
    if not first.get('meeting_series') or first.get('meeting_series') != second.get('meeting_series'):
        errors.append('same stable series required')
    actions = {a.get('action_id'): a for a in first.get('actions', []) if a.get('action_id')}
    previous = {a.get('action_id'): a for a in second.get('prior_actions', []) if a.get('action_id')}
    reviewed = [r for r in second.get('action_review_dispatches', [])
                if r.get('status') == 'recorded' and r.get('decision') in {'accept', 'return'}]
    if not actions or not set(actions) <= set(previous):
        errors.append('origin actions must be linked at the next meeting')
    def genuine_review(r):
        tid = r.get('action_id')
        prior = previous.get(tid, {})
        receipt = r.get('owner_receipt') or {}
        decision = receipt.get('review_decision') or {}
        return (tid in actions and prior.get('state') == 'submitted' and prior.get('evidence_available') is True
                and r.get('reason') and r.get('acceptance_criterion') == prior.get('acceptance_criterion')
                and r.get('evidence_refs') == prior.get('evidence_refs') and bool(prior.get('evidence_refs'))
                and receipt.get('action_id') == tid and decision.get('decision') == r.get('decision')
                and decision.get('review_source') == second.get('meeting_id'))
    if not any(genuine_review(r) for r in reviewed):
        errors.append('reasoned owner-receipted review of the same ticket required')
    if not any(a.get('state') in {'open', 'in_progress', 'waiting_for_input', 'returned'} for a in previous.values()):
        errors.append('outstanding work must remain visible')
    if not second.get('agenda') or not second['agenda'][0].startswith('Review previous work'):
        errors.append('previous work must lead the next agenda')
    if not any(e.get('status') == 'ok' and e.get('content') for e in second.get('prior_action_evidence', [])):
        errors.append('actual submitted evidence contents required')
    if any(a.get('dispatch_error') for a in actions.values()):
        errors.append('origin dispatch is still pending')
    return {'passed': not errors, 'errors': errors,
            'boundary': 'recorded meeting component; not natural Persona execution or browser acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--meeting-ids', nargs=2, required=True)
    args = parser.parse_args()
    token = os.environ.get('CONSULTATION_SERVICE_TOKEN', '')
    if not token:
        parser.error('CONSULTATION_SERVICE_TOKEN is required')
    headers = {'Authorization': f'Bearer {token}', 'X-Tenant-Id': args.tenant,
               'X-Pantheon-Tenant-Id': args.tenant, 'X-Pantheon-Service-Actor': 'consultation-workflow-executor'}
    try:
        records = []
        for mid in args.meeting_ids:
            path = '/api/consult/meetings/' + urllib.parse.quote(mid, safe='')
            req = urllib.request.Request(args.base_url.rstrip('/') + path, headers=headers, method='GET')
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read(2097153)
            if len(raw) > 2097152:
                raise ValueError('meeting response exceeds 2 MiB probe cap')
            row = json.loads(raw)
            if row.get('tenant_id') != args.tenant:
                raise ValueError('tenant mismatch')
            records.append(row)
        result = inspect_records(*records)
    except Exception as exc:
        result = {'passed': False, 'errors': [type(exc).__name__]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
