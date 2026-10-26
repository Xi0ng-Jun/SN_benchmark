"""The person's side of action requests, until the workbench page has it (design §8).

Run through `scripts/sn-workbench` against a running workbench server. It lists
pending requests, shows one, confirms (the server then executes exactly the
recorded parameters) or rejects it, and approves or rejects drafted proposals.
Agents must not use this client; nothing technically stops them, which is why
every confirmation is recorded with its actor on the request.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid
import urllib.error
import urllib.request


class Client:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, payload=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'X-Workbench': '1'}
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise SystemExit(f'{error.code}: {error.read().decode("utf-8", "replace")[:2000]}') from None


def describe(record):
    target = record['target_id'] or '（新迭代）'
    return '\n'.join([f'请求 {record["id"]} · {record["status"]}', f'  动作：{record["verb"]}  对象：{target}',
                      f'  参数：{json.dumps(record["params"], ensure_ascii=False)}',
                      f'  发起：{record["requested_by"]}  于 {record["created_at"]}', f'  理由：{record["reason"]}'])


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sn-workbench', description=__doc__)
    local = Path('configs/sn-workbench-local.json').is_file()
    parser.add_argument('--state', default=os.environ.get('SN_WORKBENCH_STATE',
                        'var/sn-workbench' if local else 'var/sn-workbench-server/state'))
    parser.add_argument('--url', default=os.environ.get('SN_WORKBENCH_URL', 'http://127.0.0.1:8091'))
    commands = parser.add_subparsers(dest='command', required=True)
    iteration = commands.add_parser('iteration').add_subparsers(dest='iteration_command', required=True)
    run_round = iteration.add_parser('round', help='start/resume a server-owned LCD round; confirms baseline dispatch')
    run_round.add_argument('iteration')
    run_round.add_argument('--rounds', type=int, default=1)
    run_round.add_argument('--request-key', default=None, help='stable key for retrying this invocation')
    iteration.add_parser('round-show').add_argument('round')
    iteration.add_parser('round-stop').add_argument('round')
    iteration.add_parser('rounds').add_argument('iteration')
    listing = commands.add_parser('requests', help='list action requests')
    listing.add_argument('--status', default='pending')
    commands.add_parser('show').add_argument('request')
    confirm = commands.add_parser('confirm', help='confirm; the server executes the recorded parameters')
    confirm.add_argument('request')
    confirm.add_argument('--note', default='')
    confirm.add_argument('--yes', action='store_true', help='skip the interactive prompt')
    reject = commands.add_parser('reject')
    reject.add_argument('request')
    reject.add_argument('--note', required=True)
    for name in ('proposal-approve', 'proposal-reject'):
        command = commands.add_parser(name)
        command.add_argument('--task', required=True)
        command.add_argument('--proposal', required=True)
        command.add_argument('--note', default='', required=name == 'proposal-reject')
    args = parser.parse_args(argv)
    client = Client(args.url, (Path(args.state) / 'access-token').read_text().strip())
    if args.command == 'iteration':
        if args.iteration_command == 'round':
            out = client.call('POST', f'/api/iterations/{args.iteration}/rounds',
                              dict(rounds=args.rounds, request_key=args.request_key or uuid.uuid4().hex))
        elif args.iteration_command == 'round-show':
            out = client.call('GET', f'/api/rounds/{args.round}')
        elif args.iteration_command == 'round-stop':
            out = client.call('POST', f'/api/rounds/{args.round}/stop', {})
        else:
            out = client.call('GET', f'/api/iterations/{args.iteration}/rounds')
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write('\n')
        return
    if args.command == 'requests':
        rows = client.call('GET', '/api/action-requests' + ('' if args.status == 'all' else f'?status={args.status}'))
        print('\n\n'.join(describe(r) for r in rows) if rows else '没有请求')
        return
    if args.command in ('proposal-approve', 'proposal-reject'):
        decision = 'approve' if args.command == 'proposal-approve' else 'reject'
        out = client.call('POST', f'/api/tasks/{args.task}/hypotheses/{args.proposal}/decision',
                          dict(decision=decision, note=args.note))
    elif args.command == 'show':
        out = client.call('GET', f'/api/action-requests/{args.request}')
    elif args.command == 'reject':
        out = client.call('POST', f'/api/action-requests/{args.request}/reject', dict(note=args.note))
    else:
        record = client.call('GET', f'/api/action-requests/{args.request}')
        print(describe(record))
        if record['status'] != 'pending':
            raise SystemExit('只有待确认的请求可以确认')
        if not args.yes:
            answer = input('确认执行？[y/N] ').strip().lower()
            if not sys.stdin.isatty():
                print()
            if answer != 'y':
                raise SystemExit('未确认，什么也没做')
        out = client.call('POST', f'/api/action-requests/{args.request}/confirm', dict(note=args.note))
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
