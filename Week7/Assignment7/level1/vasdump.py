#!/usr/bin/env python3
"""Interface skeleton. Implement /proc parsing and reports without inspection libraries."""
import argparse
from collections import Counter, defaultdict
import math
import os
import re
import subprocess
import time
import sys

FIELDS = ("Size", "Rss", "Pss", "Shared_Clean", "Shared_Dirty",
          "Private_Clean", "Private_Dirty", "Anonymous")
CATEGORIES = ("executable", "heap", "stack", "shared-library",
              "other-file", "anonymous", "special")

def parse_smaps(path):
    # TODO: parse headers and all FIELDS; preserve spaces and (deleted) in paths.
    # Each record should contain start/end, permissions, offset, dev/inode, path,
    # and a dictionary of field values in kB. Missing fields need explicit handling.
    vmas = {}
    fields = None
    with open(path, encoding='utf-8', errors='surrogateescape') as smaps:
        for line in smaps:
            if re.match(r'^[0-9a-f]+-[0-9a-f]+\s', line):
                address, perms, offset, device, inode, *name = line.rstrip('\n').split(None, 5)
                fields = {}
                vmas[address] = dict(perms=perms, offset=offset, device=device,
                                     inode=int(inode), path=name[0] if name else '', fields=fields)
            else:
                field, _, value = line.partition(':')
                if field in FIELDS:
                    if fields is None:
                        raise ValueError('smaps field before first VMA')
                    amount, unit = value.split()
                    if unit != 'kB':
                        raise ValueError(f'unexpected unit for {field}: {unit}')
                    fields[field] = int(amount)
    if not vmas:
        raise ValueError('smaps contains no VMAs')
    for address, vma in vmas.items():
        missing = set(FIELDS) - vma['fields'].keys()
        if missing:
            raise ValueError(f"{address} missing {', '.join(sorted(missing))}")
    return vmas

def classify(vma, executable):
    # TODO: implement Section 5.2 priorities, not section-name guesses.
    path = vma['path']
    if path in ('[heap]', '[stack]'):
        return path[1:-1]
    if path.startswith('['):
        return 'special'
    if not path:
        return 'anonymous'

    exe_path, exe_stat = executable
    path = path.removesuffix(' (deleted)')
    device = tuple(int(part, 16) for part in vma['device'].split(':'))
    exe_device = os.major(exe_stat.st_dev), os.minor(exe_stat.st_dev)
    if vma['inode'] and exe_stat.st_ino:
        if (device, vma['inode']) == (exe_device, exe_stat.st_ino):
            return 'executable'
    elif path == exe_path.removesuffix(' (deleted)'):
        return 'executable'
    name = os.path.basename(path)
    return 'shared-library' if name.endswith('.so') or '.so.' in name else 'other-file'


def totals(vmas):
    categories = {name: Counter() for name in CATEGORIES}
    permissions = defaultdict(Counter)
    total = Counter()
    for vma in vmas.values():
        categories[vma['category']].update(vma['fields'])
        permissions[vma['perms']].update(vma['fields'])
        total.update(vma['fields'])
    return categories, permissions, total


def print_totals(label, rows, number_format='d'):
    print(label, *(field + '_kB' for field in FIELDS), sep='\t')
    for name, values in rows:
        print(name, *(format(values[field], number_format) for field in FIELDS), sep='\t')


def print_snapshot(vmas, verbose):
    if verbose:
        print('range\tperms\toffset\tSize_kB\tRss_kB\tPss_kB\tpathname')
        for address, vma in vmas.items():
            print(address, vma['perms'], vma['offset'],
                  *(vma['fields'][field] for field in FIELDS[:3]), vma['path'], sep='\t')
        print()
    categories, permissions, total = totals(vmas)
    print_totals('category', categories.items())
    print()
    print_totals('permissions', sorted(permissions.items()))
    print()
    print_totals('process', [('TOTAL', total)])


def print_diff(before, after):
    changes = []
    for address in before.keys() | after.keys():
        old, new = before.get(address), after.get(address)
        if old == new:
            continue
        old_fields = old['fields'] if old else Counter()
        new_fields = new['fields'] if new else Counter()
        status = 'added' if old is None else 'removed' if new is None else 'changed'
        changes.append((address, status, new_fields['Size'] - old_fields['Size'],
                        new_fields['Rss'] - old_fields['Rss'], (new or old)['path']))
    changes.sort(key=lambda row: (-abs(row[3]), -abs(row[2]), int(row[0].split('-')[0], 16)))
    print('status\trange\tdelta_Size_kB\tdelta_Rss_kB\tpathname')
    for address, status, size, rss, path in changes:
        print(status, address, f'{size:+d}', f'{rss:+d}', path, sep='\t')

    old_categories, _, old_total = totals(before)
    new_categories, _, new_total = totals(after)
    old_categories['TOTAL'] = old_total
    new_categories['TOTAL'] = new_total
    rows = []
    for name, values in new_categories.items():
        values.subtract(old_categories[name])
        if name == 'TOTAL' or any(values.values()):
            rows.append((name, values))
    print()
    print_totals('category_delta', rows, '+d')


def collect(pid, command, delays):
    child = subprocess.Popen(command) if command else None
    proc = f'/proc/{child.pid if child else pid}'
    snapshots = []
    try:
        for delay in delays:
            if child:
                try:
                    status = child.wait(timeout=delay)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    raise ValueError(f'command exited before the snapshot (status {status})')
            else:
                time.sleep(delay)
            exe = proc + '/exe'
            executable = os.readlink(exe), os.stat(exe)
            vmas = parse_smaps(proc + '/smaps')
            if os.readlink(exe) != executable[0]:
                raise ValueError('target executable changed during the snapshot')
            for vma in vmas.values():
                vma['category'] = classify(vma, executable)
            snapshots.append(vmas)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
    return snapshots

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v', action='store_true')
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--diff', type=float)
    parser.add_argument('target', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if (not math.isfinite(args.delay) or args.delay < 0 or
            (args.diff is not None and (not math.isfinite(args.diff) or args.diff <= 0))):
        parser.error('delay must be nonnegative and diff must be positive')
    if not args.target:
        parser.error('provide a PID or -- command [args...]')
    # TODO: implement PID/command modes, bounded cleanup, aggregation, verbose
    # output, diff matching/sorting, and clear failures (Sections 4.1 and 5.2).
    pid, command = None, None
    if args.target[0] == '--' and len(args.target) > 1:
        command = args.target[1:]
    elif len(args.target) == 1 and re.fullmatch(r'[0-9]+', args.target[0]):
        pid = args.target[0].lstrip('0')
    if not pid and not command:
        parser.error('provide a PID or -- command [args...]')

    delays = [args.delay if command else 0]
    if args.diff is not None:
        delays.append(args.diff)
    try:
        snapshots = collect(pid, command, delays)
    except (OSError, ValueError, OverflowError, subprocess.TimeoutExpired) as error:
        print(f'vasdump: incomplete snapshot: {error}', file=sys.stderr)
        return 1

    if len(snapshots) == 1:
        print_snapshot(snapshots[0], args.v)
    else:
        if args.v:
            for index, snapshot in enumerate(snapshots, 1):
                print(f'snapshot {index}')
                print_snapshot(snapshot, True)
                print()
        print_diff(*snapshots)
    return 0

if __name__ == '__main__':
    sys.exit(main())
