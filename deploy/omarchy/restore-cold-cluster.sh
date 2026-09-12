#!/bin/bash
# One-time migration restoration. Never starts application writers.
set -euo pipefail
cd /srv/sports-harness
image=postgres@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94
archive=migration/cold-cluster-20260912.tar
test -f "$archive"
test ! -e pgdata
test ! -e migration/production-enabled
test "$(cut -d' ' -f1 migration/cold-tar-source.sha256)" = "$(cut -d' ' -f1 migration/cold-tar-target.sha256)"
date -u '+%FT%TZ' > migration/extract-start.txt
mkdir pgdata
docker run --rm --network none --read-only --entrypoint tar \
  --mount type=bind,source=/srv/sports-harness/migration/cold-cluster-20260912.tar,target=/archive,readonly \
  --mount type=bind,source=/srv/sports-harness/pgdata,target=/restore \
  "$image" --numeric-owner -xpf /archive -C /restore
date -u '+%FT%TZ' > migration/extract-finished.txt
helper=(docker run --rm --network none --read-only --mount type=bind,source=/srv/sports-harness/pgdata,target=/data,readonly)
"${helper[@]}" --entrypoint sh "$image" -c 'find /data -type f -printf "%P\t%s\n" | sort' > migration/target-file-inventory.tsv
cmp migration/source-frozen/file-inventory.tsv migration/target-file-inventory.tsv
"${helper[@]}" --entrypoint pg_controldata "$image" /data > migration/target-pg-controldata.txt
grep -Eq '^Database cluster state: +shut down$' migration/target-pg-controldata.txt
grep -Eq '^Database system identifier: +7682579637160701991$' migration/target-pg-controldata.txt
echo 'PASS extracted inventory, clean shutdown, and system identifier'
./sports-compose up -d --no-build --pull never postgres </dev/null
ready=0
for attempt in $(seq 1 60); do
  if ./sports-compose exec -T postgres pg_isready -U harness -d harness </dev/null >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
test "$ready" = 1
./sports-compose exec -T postgres psql -U harness -d harness -XqAt -v ON_ERROR_STOP=1 > migration/restored-core-manifest.json <<'SQL'
SET statement_timeout='120s';
select jsonb_build_object(
 'schema',(select version_num from alembic_version),
 'system_identifier',(select system_identifier::text from pg_control_system()),
 'counts',jsonb_build_object(
  'runs',(select count(*) from runs),'fills',(select count(*) from fills),
  'ledger',(select count(*) from ledger),'orders',(select count(*) from orders),
  'markouts',(select count(*) from markouts),'report_runs',(select count(*) from report_runs),
  'report_cells',(select count(*) from report_cells),'settlements',(select count(*) from settlements),
  'source_state',(select count(*) from source_state),'normalize_state',(select count(*) from normalize_state),
  'strategy_variants',(select count(*) from strategy_variants)),
 'invalid_indexes',(select count(*) from pg_index where not indisvalid),
 'last_tape',(select jsonb_build_object('id',id,'ts',ts) from orderbook_events order by id desc limit 1),
 'last_run',(select jsonb_build_object('id',id,'started_at',started_at,'status',status) from runs order by id desc limit 1),
 'captured_at',now());
SQL
python3 - <<'PY'
import json
from pathlib import Path
source=json.loads(Path('migration/source-frozen/core-manifest.json').read_text())
target=json.loads(Path('migration/restored-core-manifest.json').read_text())
assert source['counts']==target['counts'], (source['counts'],target['counts'])
assert source['schema']==target['schema']=='0006_quotes_run_index'
assert target['system_identifier']=='7682579637160701991'
assert target['invalid_indexes']==0
print('PASS all 11 frozen core counts, schema, system identifier, valid indexes')
print(json.dumps(target,sort_keys=True))
PY
date -u '+%FT%TZ' > migration/restore-validated.txt
echo 'RESTORE VALIDATED; application writers remain stopped'
