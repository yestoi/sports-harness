"""Exploratory local analysis of the accompanying read-only export; no database access."""
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from harness.report.stats import cluster_ci

rows = list(csv.DictReader(Path(__file__).with_name('2026-09-18-fill-review-markouts.csv').open()))
groups = defaultdict(list)
anchor_mismatches = []
for row in rows:
    if row['at_ts'] != row['filled_at']:
        anchor_mismatches.append(row['order_id'])
    if row['fair_changed'] != 't' or not row['net_markout_pts']:
        continue
    cohort = 'post_repair_fill' if int(row['first_fill_id']) > 1878 else 'pre_repair_fill'
    groups[(row['variant_id'], cohort)].append(row)
    if int(row['order_id']) > 10886:
        groups[(row['variant_id'], 'post_repair_order')].append(row)

results = {'label': 'exploratory; order-weighted and alternative weightings; not a gate report',
           'anchor_mismatches': anchor_mismatches, 'groups': []}
for (variant, cohort), sample in sorted(groups.items()):
    values = [float(r['net_markout_pts']) for r in sample]
    clusters = [r['game_id'] for r in sample]
    cells = defaultdict(list)
    games = defaultdict(list)
    for r in sample:
        cells[(r['game_id'], r['venue_market_id'], r['side'])].append(float(r['net_markout_pts']))
        games[r['game_id']].append(float(r['net_markout_pts']))
    cell_values = [sum(v)/len(v) for v in cells.values()]
    cell_clusters = [k[0] for k in cells]
    drift = [r for r in sample if r['adverse_drift_pts']]
    fresh = [r for r in sample if r['fair_age_s'] and int(r['fair_age_s']) <= 220]
    results['groups'].append({
        'variant': variant, 'cohort': cohort,
        'order_weighted_net_markout_points': asdict(cluster_ci(values, clusters)),
        'market_side_equal_weight_sensitivity': asdict(cluster_ci(cell_values, cell_clusters)),
        'game_equal_weight_mean_points': sum(sum(v)/len(v) for v in games.values())/len(games),
        'per_game': {g: {'orders': len(v), 'mean_points': sum(v)/len(v)} for g,v in sorted(games.items())},
        'adverse_drift_points': asdict(cluster_ci([float(r['adverse_drift_pts']) for r in drift], [r['game_id'] for r in drift])),
        'markout_fair_age_over_220s': sum(int(r['fair_age_s'])>220 for r in sample if r['fair_age_s']),
        'markout_fair_age_missing': sum(not r['fair_age_s'] for r in sample),
        'fresh_markout_only_selection_sensitivity': asdict(cluster_ci(
            [float(r['net_markout_pts']) for r in fresh], [r['game_id'] for r in fresh])),
    })
def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    return value

print(json.dumps(finite_json(results), indent=2, allow_nan=False))
