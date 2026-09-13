-- Read-only evidence queries for the 2026-09-13 research layer findings.
-- Run on the controller against the runtime database:
--   docker exec sports-harness-postgres-1 psql -X -U harness -d harness -At -F '|' -f - < queries.sql
-- Nothing here writes.
\echo === research_spend, every row ===
select day, kind, model, calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, searches, usd_reserved, round(usd::numeric, 4) usd
from research_spend order by day, kind, model;
\echo === veto_decisions by Chicago day, label, cache hit ===
select (signal_created_at at time zone 'America/Chicago')::date d, decision, from_cache, count(*), round(avg(confidence)::numeric, 3) avg_conf
from veto_decisions group by 1, 2, 3 order by 1, 2, 3;
\echo === veto_decisions reason codes ===
select decision, coalesce(reason_code, '') reason_code, count(*) from veto_decisions group by 1, 2 order by 3 desc;
\echo === confidence spread of decided rows ===
select decision, min(confidence), max(confidence), round(stddev(confidence)::numeric, 4) sd, count(distinct confidence) distinct_values
from veto_decisions where decision in ('proceed', 'reduce', 'veto') group by 1;
\echo === research_notes by kind ===
select kind, count(*), min(created_at), max(created_at) from research_notes group by 1 order by 1;
\echo === per-call shape by Chicago day and model (veto) ===
select (created_at at time zone 'America/Chicago')::date d, model, count(*) calls,
       round(avg(length(features::text))) features_chars,
       round(avg(jsonb_array_length(features->'fair_history'))) fair_hist_len,
       round(avg(jsonb_array_length(features->'venue_history'))) venue_hist_len,
       round(avg(jsonb_array_length(coalesce(tool_calls, '[]'::jsonb)))::numeric, 2) tool_calls,
       round(avg(latency_ms)) latency_ms,
       round(avg((usage->>'input_tokens')::int)) input_tok,
       round(avg((usage->>'cache_creation_input_tokens')::int)) cache_write_tok,
       round(avg((usage->>'cache_read_input_tokens')::int)) cache_read_tok,
       round(avg((usage->>'output_tokens')::int)) output_tok,
       round(avg(cost_usd)::numeric, 4) usd_per_call
from research_notes where kind = 'veto' group by 1, 2 order by 1, 2;
\echo === primary vs shadow decision agreement (paired on subject_id) ===
select p.output->>'decision' primary_decision, coalesce(s.output->>'decision', '(no output)') shadow_decision, count(*),
       round(avg(abs((p.output->>'confidence')::numeric - (s.output->>'confidence')::numeric)), 3) avg_conf_gap
from research_notes p
join research_notes s on s.subject_id = p.subject_id and s.kind = 'veto' and s.model = 'claude-sonnet-5'
where p.kind = 'veto' and p.model = 'claude-opus-5' group by 1, 2 order by 3 desc;
\echo === how often the model says it lacks identifiers ===
select model, count(*) total,
       count(*) filter (where output->>'reason' ~* 'no (team|matchup|venue)|identifier|lacks team|without team') says_no_identifiers
from research_notes where kind = 'veto' group by 1;
\echo === two sample primary outputs, first day and today ===
(select created_at, output::text from research_notes where kind = 'veto' and model = 'claude-opus-5' order by created_at asc limit 2)
union all
(select created_at, output::text from research_notes where kind = 'veto' and model = 'claude-opus-5' order by created_at desc limit 2);
\echo === feature keys sent to the model (one recent primary call) ===
select string_agg(k, ', ' order by k) from jsonb_object_keys((select features from research_notes where kind = 'veto' and model = 'claude-opus-5' order by created_at desc limit 1)) k;
\echo === veto call window today (Chicago) ===
select min(created_at at time zone 'America/Chicago') first_call, max(created_at at time zone 'America/Chicago') last_call, count(*) from research_notes where kind = 'veto' and created_at >= '2026-09-13 05:00+00';
\echo === decided signals that became orders and fills ===
select count(distinct v.signal_id) decided_signals, count(distinct o.id) orders, count(distinct o.id) filter (where o.filled_contracts > 0) filled_orders
from veto_decisions v left join intents i on i.signal_id = v.signal_id left join orders o on o.intent_id = i.id
where v.decision in ('proceed', 'reduce', 'veto');
\echo === veto_queue backlog ===
select count(*) rows, count(*) filter (where claimed_at is null) unclaimed, min(enqueued_at), max(enqueued_at) from veto_queue;
\echo === report_annotations ===
select report_run_id, model, left(prompt_hash, 12) prompt_hash, jsonb_array_length(bullets) bullets, round(cost_usd::numeric, 4) usd, created_at from report_annotations order by created_at;
