"""Analyze pools cache and performance."""
import json
from collections import Counter

# Load pools cache
try:
    with open('data/pools_cache.json', encoding='utf-8') as f:
        pools = json.load(f)
    print(f'Pools in cache: {len(pools)}')

    # By network
    nets = Counter(p.get('network') for p in pools)
    print('\nBy network:')
    for n, c in nets.most_common():
        print(f'  {n}: {c}')

    # By DEX
    dexes = Counter(p.get('dex') for p in pools)
    print('\nBy DEX:')
    for d, c in dexes.most_common(10):
        print(f'  {d}: {c}')

    # By version
    vers = Counter(p.get('pool_version') or 'unknown' for p in pools)
    print('\nBy version:')
    for v, c in vers.most_common():
        print(f'  {v}: {c}')

    # Sample pool
    if pools:
        p = pools[0]
        print('\nSample pool:')
        for k, v in p.items():
            print(f'  {k}: {v}')

except Exception as e:
    print(f'Error: {e}')

# Load performance
print('\n--- Performance ---')
try:
    with open('data/performance.jsonl', encoding='utf-8') as f:
        perf = [json.loads(line) for line in f if line.strip()]

    scanner_cycles = [p for p in perf if p.get('stage') == 'scanner_cycle']
    refresh_cycles = [p for p in perf if p.get('stage') == 'pool_refresh']

    print(f'Scanner cycles: {len(scanner_cycles)}')
    print(f'Refresh cycles: {len(refresh_cycles)}')

    if scanner_cycles:
        durs = [p.get('duration_ms', 0) for p in scanner_cycles]
        print(f'Scanner avg duration: {sum(durs)/len(durs):.0f}ms')
        print(f'Scanner max duration: {max(durs):.0f}ms')

        pools_processed = [p.get('items_total', 0) for p in scanner_cycles]
        print(f'Avg pools per cycle: {sum(pools_processed)/len(pools_processed):.0f}')

    if refresh_cycles:
        durs = [p.get('duration_ms', 0) for p in refresh_cycles]
        print(f'Refresh avg duration: {sum(durs)/len(durs)/1000:.1f}s')

except Exception as e:
    print(f'Error: {e}')
