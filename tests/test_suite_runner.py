"""The shared suite runner: per-database range locks, sharded full suites, merged receipts.

No shared slot, no application database: the lock file and state directory are temporary,
pytest children are stubbed, and the only real kernel call is the file lock itself.
"""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
TREE = "t" * 40


def load():
    spec = importlib.util.spec_from_file_location("test_suite", ROOT / "scripts/test-suite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lock_offsets_are_stable_and_distinct_per_database():
    runner = load()
    assert runner.lock_offset("harness_test_main") == runner.lock_offset("harness_test_main")
    assert runner.lock_offset("harness_test_main") != runner.lock_offset("harness_test_fix_1")
    assert 0 <= runner.lock_offset("x") < runner.LOCK_SPAN


def test_database_lock_blocks_the_same_database_only(tmp_path):
    runner = load()
    lock_file = tmp_path / "lock"
    lock_file.touch()
    with lock_file.open("a+") as lock:
        runner.acquire(lock.fileno(), "harness_test_main")
        probe = (
            "import fcntl, struct, sys\n"
            "handle = open(sys.argv[1], 'a+'); fd = handle.fileno()\n"
            "def held(offset):\n"
            "    data = struct.pack('hhqqi', fcntl.F_WRLCK, 0, offset, 1, 0) + bytes(4)\n"
            f"    got = fcntl.fcntl(fd, {runner.F_OFD_GETLK}, data)\n"
            "    return struct.unpack('hhqqi', got[:28])[0] != fcntl.F_UNLCK\n"
            "print(held(int(sys.argv[2])), held(int(sys.argv[3])))\n"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", probe, str(lock_file),
             str(runner.lock_offset("harness_test_main")),
             str(runner.lock_offset("harness_test_other"))], text=True)
    assert out.split() == ["True", "False"]


def test_full_suite_shards_pin_schema_tests_to_the_base_database_and_balance_the_rest(tmp_path):
    runner = load()
    counts = {"tests/test_schema.py": 68, "tests/test_alembic.py": 20, "tests/test_big.py": 100,
              "tests/test_mid.py": 50, "tests/test_small.py": 5, "tests/test_tiny.py": 1}
    plan = runner.plan_shards(counts, shards=3, base="harness_test_x")
    assert [shard.database for shard in plan] == ["harness_test_x", "harness_test_x_p2", "harness_test_x_p3"]
    assert {"tests/test_schema.py", "tests/test_alembic.py"} <= set(plan[0].files)
    assigned = sorted(path for shard in plan for path in shard.files)
    assert assigned == sorted(counts)
    loads = [sum(counts[path] for path in shard.files) for shard in plan]
    assert max(loads) - min(loads) <= 100


def test_plan_prefers_recorded_durations_over_test_counts():
    runner = load()
    counts = {"tests/test_schema.py": 1, "tests/test_slow.py": 1, "tests/test_a.py": 50, "tests/test_b.py": 50}
    weights = {"tests/test_slow.py": 600.0, "tests/test_a.py": 1.0, "tests/test_b.py": 1.0}
    plan = runner.plan_shards(counts, shards=2, base="db", weights=weights)
    slow = next(shard for shard in plan if "tests/test_slow.py" in shard.files)
    assert slow.files == ["tests/test_slow.py"]  # the slow file gets a shard to itself
    assert sorted(path for shard in plan for path in shard.files) == sorted(counts)


def test_record_durations_sums_junit_times_per_file_and_merges_the_previous_record(tmp_path):
    runner = load()
    junit = tmp_path / "shard1.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_a" name="t1" time="1.5"/>'
                     '<testcase classname="tests.test_a" name="t2" time="2.0"/>'
                     '<testcase classname="tests.test_b.TestX" name="t3" time="4.0"/></testsuite></testsuites>')
    record = tmp_path / "test-durations.json"
    record.write_text(json.dumps({"tests/test_old.py": 9.0, "tests/test_a.py": 100.0}))  # legacy flat form
    runner.record_durations([junit], record)
    written = json.loads(record.read_text())
    assert written["files"] == {"tests/test_old.py": 9.0, "tests/test_a.py": 3.5, "tests/test_b.py": 4.0}
    assert written["nodes"] == {"tests/test_a.py::t1": 1.5, "tests/test_a.py::t2": 2.0, "tests/test_b.py::TestX::t3": 4.0}


def test_record_durations_adds_a_split_files_shards_together_instead_of_keeping_the_last(tmp_path):
    runner = load()
    first = tmp_path / "shard1.xml"
    first.write_text('<testsuites><testsuite><testcase classname="tests.test_heavy" name="slow" time="500.0"/>'
                     '</testsuite></testsuites>')
    second = tmp_path / "shard2.xml"
    second.write_text('<testsuites><testsuite><testcase classname="tests.test_heavy" name="quick" time="7.0"/>'
                      '<testcase classname="tests.test_heavy" name="quicker" time="3.0"/></testsuite></testsuites>')
    record = tmp_path / "test-durations.json"
    runner.record_durations([first, second], record)
    assert json.loads(record.read_text())["files"] == {"tests/test_heavy.py": 510.0}


def test_plan_trusts_the_node_records_when_the_file_sum_is_smaller_than_they_are():
    runner = load()
    counts = {"tests/test_schema.py": 1, "tests/test_heavy.py": 3, "tests/test_a.py": 5, "tests/test_b.py": 5}
    weights = {"tests/test_schema.py": 30.0, "tests/test_heavy.py": 60.0, "tests/test_a.py": 300.0, "tests/test_b.py": 300.0}
    nodes = {"tests/test_heavy.py::slow": 500.0, "tests/test_heavy.py::mid": 300.0, "tests/test_heavy.py::quick": 60.0}
    plan = runner.plan_shards(counts, shards=4, base="db", weights=weights, nodes=nodes)
    units = [unit for shard in plan for unit in shard.files]
    assert "tests/test_heavy.py::slow" in units and "tests/test_heavy.py" not in units


def test_a_file_heavier_than_a_shard_is_split_into_its_recorded_nodes_plus_a_deselected_remainder():
    runner = load()
    counts = {"tests/test_schema.py": 1, "tests/test_heavy.py": 7, "tests/test_a.py": 50, "tests/test_b.py": 50}
    weights = {"tests/test_schema.py": 30.0, "tests/test_heavy.py": 850.0, "tests/test_a.py": 300.0, "tests/test_b.py": 300.0}
    nodes = {"tests/test_heavy.py::test_slow": 470.0, "tests/test_heavy.py::test_slower": 320.0,
             "tests/test_heavy.py::test_quick": 60.0, "tests/test_a.py::test_x": 300.0}
    plan = runner.plan_shards(counts, shards=4, base="db", weights=weights, nodes=nodes)
    units = [unit for shard in plan for unit in shard.files]
    assert "tests/test_heavy.py::test_slow" in units and "tests/test_heavy.py::test_slower" in units
    remainder = next(u for u in units if u.startswith("tests/test_heavy.py --deselect"))
    assert remainder == ("tests/test_heavy.py --deselect tests/test_heavy.py::test_slow"
                         " --deselect tests/test_heavy.py::test_slower")
    assert "tests/test_heavy.py" not in units  # never both whole and split
    assert "tests/test_a.py" in units  # under the threshold: stays whole
    slow_shard = next(shard for shard in plan if "tests/test_heavy.py::test_slow" in shard.files)
    slower_shard = next(shard for shard in plan if "tests/test_heavy.py::test_slower" in shard.files)
    assert slow_shard is not slower_shard
    assert slow_shard.files == ["tests/test_heavy.py::test_slow"]


def test_a_heavy_file_without_node_records_stays_whole():
    runner = load()
    counts = {"tests/test_schema.py": 1, "tests/test_heavy.py": 7, "tests/test_a.py": 5}
    weights = {"tests/test_heavy.py": 850.0, "tests/test_a.py": 30.0, "tests/test_schema.py": 30.0}
    plan = runner.plan_shards(counts, shards=3, base="db", weights=weights, nodes={})
    assert sorted(u for shard in plan for u in shard.files) == sorted(counts)


def test_load_record_reads_the_seed_when_the_state_directory_has_no_record(tmp_path, monkeypatch):
    runner = load()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test-durations.json").write_text(json.dumps(
        {"files": {"tests/test_a.py": 5.0}, "nodes": {"tests/test_a.py::t": 5.0}}))
    files, nodes = runner.load_record(tmp_path / "state/test-durations.json")
    assert files == {"tests/test_a.py": 5.0} and nodes == {"tests/test_a.py::t": 5.0}
    (tmp_path / "state").mkdir()
    (tmp_path / "state/test-durations.json").write_text(json.dumps({"tests/test_b.py": 7.0}))
    files, nodes = runner.load_record(tmp_path / "state/test-durations.json")
    assert files == {"tests/test_b.py": 7.0} and nodes == {}  # the host record wins over the seed


def test_a_database_name_too_long_for_shard_suffixes_is_refused():
    runner = load()
    with pytest.raises(SystemExit):
        runner.plan_shards({"tests/test_a.py": 1, "tests/test_b.py": 1}, shards=2, base="x" * 61)


def test_shard_count_never_exceeds_the_number_of_files():
    runner = load()
    plan = runner.plan_shards({"tests/test_one.py": 3}, shards=6, base="db")
    assert len(plan) == 1 and plan[0].database == "db"


def test_test_counts_read_sync_async_and_method_tests(tmp_path):
    runner = load()
    source = tmp_path / "test_x.py"
    source.write_text("def test_a():\n    pass\nasync def test_b():\n    pass\n"
                      "class TestC:\n    def test_d(self):\n        pass\n    def helper(self):\n        pass\n")
    assert runner.count_tests(source) == 3


def _git_stub(args, **kwargs):
    if args[0] == "git":
        if args[1] == "branch":
            return "main\n"
        if args[1] == "status":
            return ""
        if args[2:] == ["HEAD^{tree}"]:
            return TREE + "\n"
        return HEAD + "\n"
    return f"postgresql+psycopg://test:test@localhost:5433/{args[2]}\n"


def _runner_env(monkeypatch, tmp_path, argv):
    runner = load()
    state = tmp_path / "state"
    monkeypatch.setenv("SPORTS_TEST_STATE_DIR", str(state))
    monkeypatch.setenv("SPORTS_TEST_LOCK_FILE", str(tmp_path / "lock"))
    monkeypatch.setenv("TEST_DB", "harness_test_main")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(runner.sys, "argv", ["test-suite.py", *argv])
    monkeypatch.setattr(runner.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runner.subprocess, "check_output", _git_stub)
    return runner, state


def test_sharded_full_suite_merges_exit_codes_and_records_the_tree(monkeypatch, tmp_path):
    runner, state = _runner_env(monkeypatch, tmp_path, [])
    monkeypatch.setenv("TEST_SHARDS", "2")
    monkeypatch.setattr(runner, "discover", lambda: {"tests/test_schema.py": 4, "tests/test_a.py": 4})
    launches = []

    def popen(args, **kwargs):
        launches.append((args, kwargs))
        index = len(launches)
        kwargs["stdout"].write(f"== {index} passed, 1 xfailed in 2.00s ==\n")
        return SimpleNamespace(pid=100 + index, wait=lambda: 0 if index == 1 else 3)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.main() == 3
    assert not (state / "test-durations.json").exists()  # an interrupted or crashed shard records nothing
    databases = sorted(kw["env"]["DATABASE_URL_TEST"].rsplit("/", 1)[1] for _, kw in launches)
    assert databases == ["harness_test_main", "harness_test_main_p2"]
    assert all(kw["start_new_session"] and len(kw["pass_fds"]) == 1 for _, kw in launches)
    receipt = json.loads((state / "test-harness_test_main.json").read_text())
    assert receipt["exit_code"] == 3 and receipt["scope"] == [] and receipt["tree"] == TREE
    assert receipt["head"] == HEAD and receipt["head_after"] == HEAD
    assert sorted(shard["database"] for shard in receipt["shards"]) == databases
    assert sorted(shard["exit_code"] for shard in receipt["shards"]) == [0, 3]
    junits = [args[args.index("--junit-xml") + 1] for args, _ in launches]
    assert len(junits) == 2 and all(Path(j).parent == state for j in junits)


def test_sharded_run_records_durations_for_the_next_plan(monkeypatch, tmp_path):
    runner, state = _runner_env(monkeypatch, tmp_path, [])
    monkeypatch.setenv("TEST_SHARDS", "2")
    monkeypatch.setattr(runner, "discover", lambda: {"tests/test_schema.py": 4, "tests/test_a.py": 4})

    def popen(args, **kwargs):
        junit = Path(args[args.index("--junit-xml") + 1])
        name = "test_schema" if "tests/test_schema.py" in args else "test_a"
        junit.write_text(f'<testsuites><testsuite><testcase classname="tests.{name}" name="t" time="2.5"/>'
                         '</testsuite></testsuites>')
        kwargs["stdout"].write("== 1 passed in 2.50s ==\n")
        return SimpleNamespace(pid=1, wait=lambda: 0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.main() == 0
    record = json.loads((state / "test-durations.json").read_text())
    assert record["files"] == {"tests/test_schema.py": 2.5, "tests/test_a.py": 2.5}
    assert record["nodes"] == {"tests/test_schema.py::t": 2.5, "tests/test_a.py::t": 2.5}


def test_sharded_full_suite_launches_split_units_as_pytest_arguments(monkeypatch, tmp_path):
    runner, state = _runner_env(monkeypatch, tmp_path, [])
    monkeypatch.setenv("TEST_SHARDS", "3")
    monkeypatch.setattr(runner, "discover", lambda: {"tests/test_schema.py": 1, "tests/test_heavy.py": 3, "tests/test_a.py": 2})
    state.mkdir(parents=True)
    (state / "test-durations.json").write_text(json.dumps({
        "files": {"tests/test_schema.py": 10.0, "tests/test_heavy.py": 900.0, "tests/test_a.py": 20.0},
        "nodes": {"tests/test_heavy.py::test_slow": 600.0, "tests/test_heavy.py::test_mid": 200.0,
                  "tests/test_heavy.py::test_quick": 100.0}}))
    launches = []

    def popen(args, **kwargs):
        launches.append(args)
        kwargs["stdout"].write("== 1 passed in 1.00s ==\n")
        return SimpleNamespace(pid=1, wait=lambda: 0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.main() == 0
    tails = [args[args.index("-ra") + 3:] for args in launches]  # after -ra, --junit-xml, <path>
    assert ["tests/test_heavy.py::test_slow"] in tails
    assert any(t[:3] == ["tests/test_heavy.py", "--deselect", "tests/test_heavy.py::test_slow"] for t in tails)


def test_sharded_output_ends_with_one_combined_summary_line(monkeypatch, tmp_path, capsys):
    runner, state = _runner_env(monkeypatch, tmp_path, [])
    monkeypatch.setenv("TEST_SHARDS", "2")
    monkeypatch.setattr(runner, "discover", lambda: {"tests/test_schema.py": 4, "tests/test_a.py": 4})
    summaries = iter(["== 10 passed, 1 xfailed in 2.00s ==\n", "== 1 failed, 5 passed in 3.50s ==\n"])

    def popen(args, **kwargs):
        kwargs["stdout"].write(next(summaries))
        return SimpleNamespace(pid=7, wait=lambda: 0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    runner.main()
    lines = capsys.readouterr().out.rstrip().splitlines()
    assert lines[-1].startswith("=") and "1 failed, 15 passed, 1 xfailed" in lines[-1] and "2 shards" in lines[-1]
    assert "10 passed, 1 xfailed" in "\n".join(lines) and "5 passed" in "\n".join(lines)


def test_scoped_run_is_one_process_on_the_base_database_with_a_tree(monkeypatch, tmp_path):
    runner, state = _runner_env(monkeypatch, tmp_path, ["--", "tests/test_one.py"])
    monkeypatch.setenv("TEST_SHARDS", "6")
    launches = []

    def popen(args, **kwargs):
        launches.append((args, kwargs))
        return SimpleNamespace(pid=1, wait=lambda: 0)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.main() == 0
    assert len(launches) == 1 and launches[0][1]["env"]["DATABASE_URL_TEST"].endswith("/harness_test_main")
    receipt = json.loads((state / "test-harness_test_main-scoped.json").read_text())
    assert receipt["scope"] == ["tests/test_one.py"] and receipt["tree"] == TREE and "shards" not in receipt


def test_scoped_run_never_overwrites_the_full_suite_receipt(monkeypatch, tmp_path):
    runner, state = _runner_env(monkeypatch, tmp_path, ["tests/test_one.py"])
    state.mkdir(parents=True)
    (state / "test-harness_test_main.json").write_text('{"scope": [], "exit_code": 0}')
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=1, wait=lambda: 0))
    assert runner.main() == 0
    assert json.loads((state / "test-harness_test_main.json").read_text()) == {"scope": [], "exit_code": 0}
