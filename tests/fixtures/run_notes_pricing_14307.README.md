# `run_notes_pricing_14307.json` -- what is recorded and what is constructed

The six `elapsed_ms` values, `budget_s`, `gaps`, `no_sharp`, the `order`'s **lead pair** and the
candidate/rejected **endpoints** (9-166 candidates, 558-1,376 rejected) are the 6D addendum's
live fact (a), read from production by the controller and treated as data. The five tail entries
of `order` are one legal rotation -- `pricing_order` rotates the five secondaries by
`run_id % 5`, which for 14307 is 2, and the active set's own order is not recorded -- so **no
test asserts the tail's identity**; the per-variant pairs between the endpoints, `variant_ms` and
`fair_direct` are constructed inside the recorded envelope (the seven `variant_ms` values sum to
9,016, within the `variants_direct` + `variants_derived` total, and `sharp_plus_derived` carries
the derived pass).

The sibling `run_notes_pricing_exhausted.json` is the pre-fix-48 exhausted shape quoted in
`harness/strategy/pipeline.py`'s module docstring.

Neither file is edited again by any later task: Task 5 **adds** `units`, `remaining_ms`, `cause`,
`variant_ms_rescore` and `rescore_suppressed` to the shape the code writes, and proves the
readers on both the old fixture (no such keys) and the new shape it builds in its test. That is
the point of keeping this file frozen -- it is what a reader will meet in `runs` rows written
before the 6D deploy.
