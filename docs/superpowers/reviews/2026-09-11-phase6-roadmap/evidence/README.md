**Evidence and reproduction notes**

The roadmap and reconciliation are proposed documents, not implementation changes. Their inputs are preserved byte for byte under [inputs/other-review](inputs/other-review/README.md) and [inputs/our-review](inputs/our-review/REVIEW.md). Original input documents are historical records and may retain links to their original locations or contain claims corrected by the reconciliation.

The additional [execution probes](execution-reconciliation-probes.py) ran against reviewed HEAD `6eed2d8d49a7bf1f107dfd83141b8eeef556b620` with database persistence mocked. [Captured output](execution-reconciliation-output.txt) demonstrates the defects and the limits of their attribution. The probes do not validate a repair, replay production order 157, execute SQL, or establish that the full gate passes. They depend on the repository and its installed Python environment:

```sh
/Users/trey/dev/sports/.venv/bin/python /Users/trey/dev/sports-roadmap-2026-09-11/evidence/execution-reconciliation-probes.py --repo /Users/trey/dev/sports
```

[The V6 finding](other-review-V6.json) preserves the other review's own rejection of its 0.8-cent figure. [The capacity recount](cap-recount.json) records a calculation from its saved D3 table. Production observations come from the original reviews; this reconciliation did not refresh them.

Source links in the reconciliation point to the working checkout for convenient reading. The following frozen files preserve the reviewed code if the checkout changes. Other cited evidence and source snapshots are also present in the copied original review package. [The manifest](../manifest.json) records original input locations, source identity and SHA-256 hashes for the bundle. Hashes attest to file identity, not the truth of a claim.

- [docs/runbooks/backups.md](source/docs/runbooks/backups.md)
- [docs/superpowers/autopilot/roadmap.md](source/docs/superpowers/autopilot/roadmap.md)
- [harness/dashboard/scheduler.py](source/harness/dashboard/scheduler.py)
- [harness/dashboard/snapshots/pulse.py](source/harness/dashboard/snapshots/pulse.py)
- [harness/dashboard/snapshots/study.py](source/harness/dashboard/snapshots/study.py)
- [harness/dashboard/snapshots/ticket.py](source/harness/dashboard/snapshots/ticket.py)
- [harness/execution/book.py](source/harness/execution/book.py)
- [harness/execution/fills.py](source/harness/execution/fills.py)
- [harness/execution/loop.py](source/harness/execution/loop.py)
- [harness/execution/plan.py](source/harness/execution/plan.py)
- [harness/execution/store.py](source/harness/execution/store.py)
- [harness/recorder/ws_sink.py](source/harness/recorder/ws_sink.py)
- [harness/replay.py](source/harness/replay.py)
- [harness/report/gate.py](source/harness/report/gate.py)
- [harness/report/tables.py](source/harness/report/tables.py)
- [harness/report/weekly.py](source/harness/report/weekly.py)
- [harness/research/annotate.py](source/harness/research/annotate.py)
- [harness/settlement/report_wtd.py](source/harness/settlement/report_wtd.py)
- [tests/test_fills.py](source/tests/test_fills.py)
- [tests/test_fills_tape.py](source/tests/test_fills_tape.py)
