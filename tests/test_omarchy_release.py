"""Pure fault-injection tests: no subprocess, network, Docker, or database operations.

The release scripts are imported as modules. All external process/network boundaries are
replaced; only temporary files are written. Failing assertions describe required release
contracts and must not be converted into xfails merely to accept a draft.
"""
import copy
import fnmatch
from datetime import datetime, timezone
import importlib.util
import io
import json
import signal
from pathlib import Path
import subprocess
import tarfile
import urllib.error
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40
TREE = "t" * 40
SHA = "aaaaaaa"
OLD = "b0a3991"
PG_IMAGE = "postgres@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release(monkeypatch, tmp_path):
    module = load_script("release-omarchy")
    runtime, home = tmp_path / "runtime", tmp_path / "home"
    runtime.mkdir()
    (runtime / "migration").mkdir()
    (runtime / "migration/production-enabled").touch()
    cache = home / ".cache/sports-harness/test-state"
    cache.mkdir(parents=True)
    env = {"LIVE_TRADING": "0", "HARNESS_MODE": "paper", "RFQ_LISTENER_ENABLED": "0",
           "DB_BUDGET_GB": "600", "BUILD_SHA": OLD, "BUILD_TIME": "2026-09-11T00:00:00Z"}
    before = {"services": {name: {"image": f"sports-migration/app:{OLD}",
                                  "environment": dict(env)}
                           for name in module.APPS + ["app-ws"]}}
    before["services"]["postgres"] = {"image": PG_IMAGE}
    before["services"]["app-backup"] = {"image": PG_IMAGE}
    base = json.dumps({"services": {}})
    (runtime / "docker-compose.yml").write_text(base)
    (runtime / "compose.omarchy.yml").write_text(json.dumps(before))
    (runtime / ".env").write_text("PRIVATE_VALUE=must-remain-byte-identical\n")
    receipt = {"head": HEAD, "head_after": HEAD, "exit_code": 0, "scope": [],
               "dirty_before": "", "dirty_after": "", "branch": "main",
               "database": "harness_test_main", "pytest_addopts": "", "pytest_plugins": ""}
    test_receipt = cache / "test-harness_test_main.json"
    test_receipt.write_text(json.dumps(receipt))
    state = SimpleNamespace(module=module, runtime=runtime, before=before, home=home,
                            test_receipt=test_receipt, receipt=receipt, calls=[], health_calls=[],
                            failure=None, failed=False, dirty="", head=HEAD, tree=TREE, old=OLD,
                            touched=[], mutate_candidate=None)
    monkeypatch.setattr(module, "RUNTIME", runtime)
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("SPORTS_TEST_STATE_DIR", str(cache))
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=75, total=100))
    monkeypatch.setattr(module, "health", lambda: {"build": state.old, "status": "ok"})
    monkeypatch.setattr(module, "game_window", lambda mode: {"blocked": False, "nfl_blocked": False})

    def git(*args):
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain"):
            return state.dirty
        if args == ("rev-parse", "--short", "HEAD"):
            return SHA
        if args == ("rev-parse", "HEAD"):
            return state.head
        if args == ("rev-parse", "HEAD^{tree}"):
            return state.tree
        if args[:1] == ("cat-file",):
            return ""
        if args[:1] == ("diff",):
            paths = args[args.index("--") + 1:]
            return "\n".join(path for path in state.touched if any(
                path == spec or path.startswith(spec.rstrip("/") + "/") or fnmatch.fnmatch(path, spec)
                for spec in paths))
        raise AssertionError(f"Unexpected git command: {args}")

    def run(args, **kwargs):
        args = [str(a) for a in args]
        state.calls.append(args)
        if "config" in args:
            override = Path(args[max(i for i, a in enumerate(args) if a == "-f") + 1])
            rendered = copy.deepcopy(before)
            if override.name == "candidate-override.json":
                rendered = json.loads(override.read_text())
                if state.mutate_candidate:
                    state.mutate_candidate(rendered)
            return json.dumps(rendered)
        if args[:3] == ["docker", "image", "inspect"]:
            return "sha256:old-image" if OLD in args[3] else "sha256:new-image"
        stage = ("stop" if "stop" in args else "migrate" if "migrate" in args else
                 "init-db" if "init-db" in args else "variants" if "variants" in args else
                 "seed-teams" if "seed-teams" in args else "up" if "up" in args else None)
        if state.failure and state.failure == stage and not state.failed:
            state.failed = True
            raise subprocess.CalledProcessError(1, args)
        return ""

    def archive(args, **kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1)  # Candidate immutable tag does not exist.
        assert args == ["git", "archive", HEAD], "All process execution must remain mocked"
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as stream:
            content = base.encode()
            info = tarfile.TarInfo("docker-compose.yml")
            info.size = len(content)
            stream.addfile(info, io.BytesIO(content))
        return SimpleNamespace(returncode=0)

    def wait_healthy(sha, services, **kwargs):
        state.health_calls.append((sha, list(services), kwargs))
        if state.failure == "health" and not state.failed:
            state.failed = True
            raise RuntimeError("injected health failure")
        return {"build": sha, "status": "ok"}

    monkeypatch.setattr(module, "git", git)
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module.subprocess, "run", archive)
    monkeypatch.setattr(module, "wait_healthy", wait_healthy)
    return state


def release_receipt(state):
    paths = list((state.runtime / "releases").glob("*/receipt.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_app_only_preserves_websocket_image_stamp_and_runtime_env(release):
    original_env = (release.runtime / ".env").read_bytes()
    release.module.deploy("app")
    overlay = json.loads((release.runtime / "compose.omarchy.yml").read_text())
    assert overlay["services"]["app-ws"] == release.before["services"]["app-ws"]
    assert (release.runtime / ".env").read_bytes() == original_env
    for call in release.calls:
        if "stop" in call or "up" in call:
            assert "app-ws" not in call
        assert "migrate" not in call and "init-db" not in call
    assert release_receipt(release)["status"] == "healthy"


@pytest.mark.parametrize("stage", ["stop", "migrate", "init-db", "variants", "up", "health", "promotion"])
def test_failure_restores_both_configs_and_restarts_old_apps(release, monkeypatch, stage):
    originals = {name: (release.runtime / name).read_bytes()
                 for name in ("docker-compose.yml", "compose.omarchy.yml", ".env")}
    release.failure = stage
    if stage == "promotion":
        real_copy = release.module.atomic_copy

        def fail_second_promotion(source, dest):
            if source.name == "candidate-override.json" and not release.failed:
                release.failed = True
                raise OSError("injected override promotion failure")
            return real_copy(source, dest)

        monkeypatch.setattr(release.module, "atomic_copy", fail_second_promotion)
    with pytest.raises((subprocess.CalledProcessError, RuntimeError, OSError)):
        release.module.deploy("full")
    assert release.failed
    for name, expected in originals.items():
        assert (release.runtime / name).read_bytes() == expected
    up = [call for call in release.calls if "up" in call]
    assert up and all(service in up[-1] for service in release.module.APPS + ["app-ws"])
    assert release_receipt(release)["status"] == "failed-old-apps-restored"


def test_rollback_does_not_claim_restored_until_old_release_is_healthy(release):
    release.failure = "health"
    with pytest.raises(RuntimeError):
        release.module.deploy("app")
    assert release.health_calls[-1][0] == OLD


@pytest.mark.parametrize("field,value", [
    ("head", "c" * 40), ("head_after", "c" * 40), ("exit_code", 1),
    ("scope", ["tests/test_one.py"]), ("dirty_before", " M harness/x.py"),
    ("dirty_after", "?? untracked.py"),
])
def test_stale_dirty_partial_or_failed_suite_receipt_is_rejected(release, field, value):
    release.receipt[field] = value
    release.test_receipt.write_text(json.dumps(release.receipt))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")
    assert not any("build" in call or "stop" in call for call in release.calls)


def test_branch_receipt_with_the_same_tree_covers_the_deploy(release):
    """A rebased branch suite on a byte-identical tree is the main receipt; no rerun on main."""
    release.test_receipt.unlink()
    branch = dict(release.receipt, head="c" * 40, head_after="c" * 40, branch="fix-x",
                  database="harness_test_fix_x", tree=TREE)
    (release.test_receipt.parent / "test-harness_test_fix_x.json").write_text(json.dumps(branch))
    release.module.deploy("app")
    written = release_receipt(release)
    assert written["status"] == "healthy"
    assert written["suite_receipt"]["database"] == "harness_test_fix_x" and written["suite_receipt"]["tree"] == TREE


def test_the_exact_commit_receipt_wins_over_a_tree_match_and_ties_never_crash(release):
    twin = dict(release.receipt, head="c" * 40, head_after="c" * 40, tree=TREE, database="harness_test_fix_x")
    (release.test_receipt.parent / "test-harness_test_fix_x.json").write_text(json.dumps(twin))
    (release.test_receipt.parent / "test-harness_test_fix_y.json").write_text(json.dumps(twin))
    release.module.deploy("app")
    assert release_receipt(release)["suite_receipt"]["database"] == "harness_test_main"


def test_receipt_with_neither_the_head_nor_the_tree_is_rejected(release):
    release.test_receipt.unlink()
    branch = dict(release.receipt, head="c" * 40, head_after="c" * 40, tree="u" * 40)
    (release.test_receipt.parent / "test-harness_test_fix_x.json").write_text(json.dumps(branch))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")
    assert not any("build" in call or "stop" in call for call in release.calls)


def test_tree_matched_receipt_must_still_be_clean_and_complete(release):
    release.test_receipt.unlink()
    branch = dict(release.receipt, head="c" * 40, head_after="c" * 40, tree=TREE,
                  dirty_before="?? .superpowers-report-x.md")
    (release.test_receipt.parent / "test-harness_test_fix_x.json").write_text(json.dumps(branch))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")


def test_sharded_receipt_with_a_failed_shard_is_rejected(release):
    release.receipt["shards"] = [{"database": "harness_test_main", "exit_code": 0},
                                 {"database": "harness_test_main_p2", "exit_code": 1}]
    release.receipt["exit_code"] = 1
    release.test_receipt.write_text(json.dumps(release.receipt))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")


def test_dirty_checkout_is_rejected_before_build_or_stop(release):
    release.dirty = "?? new-file.py"
    with pytest.raises(RuntimeError, match="clean main"):
        release.module.deploy("app")
    assert release.calls == []


def test_full_path_diff_cannot_restart_apps_in_college_exception(release):
    release.touched = ["harness/recorder/ws_sink.py"]
    with pytest.raises(RuntimeError, match="Full release"):
        release.module.deploy("app")
    assert not any("stop" in call for call in release.calls)


def test_preserved_service_config_drift_aborts_before_stop(release):
    release.mutate_candidate = lambda config: config["services"]["postgres"].update(command=["postgres", "-c", "work_mem=1GB"])
    with pytest.raises(RuntimeError, match="postgres config changed"):
        release.module.deploy("full")
    assert not any("stop" in call for call in release.calls)


def test_full_release_retains_required_variant_registration(release):
    release.module.deploy("full")
    assert any(call[-2:] == ["variants", "register"] for call in release.calls)


def test_redeployment_does_not_overwrite_the_previous_image_tag(release):
    release.old = SHA
    for service in release.module.APPS + ["app-ws"]:
        release.before["services"][service]["image"] = f"sports-release/app:{SHA}"
    (release.runtime / "compose.omarchy.yml").write_text(json.dumps(release.before))
    try:
        release.module.deploy("app")
    except RuntimeError:
        pass  # Refusing a no-change redeployment is also a safe outcome.
    builds = [call for call in release.calls if call[:2] == ["docker", "build"]]
    assert all(call[call.index("-t") + 1] != f"sports-release/app:{SHA}" for call in builds)


@pytest.mark.parametrize("mode,day,blocked,nfl,allowed", [
    ("app", "2026-09-10T23:00:00+00:00", True, False, True),
    ("app", "2026-09-11T23:00:00+00:00", True, False, True),
    ("app", "2026-09-12T23:00:00+00:00", True, False, True),
    ("app", "2026-09-13T04:59:59+00:00", True, False, True),  # Saturday CT
    ("app", "2026-09-13T05:00:00+00:00", True, False, False), # Sunday CT
    ("app", "2026-09-12T23:00:00+00:00", True, True, False),
    ("full", "2026-09-12T23:00:00+00:00", True, False, False),
    ("full", "2026-09-13T23:00:00+00:00", False, False, True),
])
def test_window_exception_uses_chicago_day_and_never_overrides_nfl(mode, day, blocked, nfl, allowed):
    module = load_script("release-omarchy")
    assert module.window_allowed(mode, datetime.fromisoformat(day),
                                 {"blocked": blocked, "nfl_blocked": nfl}) is allowed


def test_suite_cannot_issue_full_receipt_for_environment_filtered_pytest(monkeypatch, tmp_path):
    module = load_script("test-suite")
    monkeypatch.setenv("TEST_SHARDS", "1")
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("SPORTS_TEST_STATE_DIR", str(tmp_path / ".cache/sports-harness/test-state"))
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_this_test")
    monkeypatch.setenv("TEST_DB", "harness_test_main")
    monkeypatch.setattr(module.sys, "argv", ["test-suite.py"])
    monkeypatch.setattr(module.signal, "signal", lambda *a: None)
    launches = []

    def check_output(args, **kwargs):
        if args[0] == "git":
            return "main\n" if args[1] == "branch" else "" if args[1] == "status" else HEAD + "\n"
        assert args[1:] == ["scripts/testdb.py", "harness_test_main"]
        return "postgresql+psycopg://test:test@localhost:5433/harness_test_main\n"

    def popen(args, **kwargs):
        launches.append((args, kwargs))
        return SimpleNamespace(pid=12345, wait=lambda: 0)

    monkeypatch.setattr(module.subprocess, "check_output", check_output)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    try:
        module.main()
    except (RuntimeError, SystemExit):
        return  # Explicit rejection of hidden pytest selectors is safe.
    receipt = json.loads((tmp_path / ".cache/sports-harness/test-state/test-harness_test_main.json").read_text())
    hidden_filter_removed = not launches[0][1]["env"].get("PYTEST_ADDOPTS")
    declared_filter = receipt.get("pytest_addopts") == "-k only_this_test"
    assert hidden_filter_removed or declared_filter or receipt["scope"] != [], (
        "Filtered pytest must never attest an unfiltered full suite")


def test_suite_child_inherits_the_lock_and_its_exit_code_is_recorded(monkeypatch, tmp_path):
    module = load_script("test-suite")
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("SPORTS_TEST_STATE_DIR", str(tmp_path / ".cache/sports-harness/test-state"))
    monkeypatch.setenv("TEST_DB", "harness_test_main")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(module.sys, "argv", ["test-suite.py", "--", "tests/test_one.py"])
    monkeypatch.setattr(module.signal, "signal", lambda *a: None)
    launches = []

    def check_output(args, **kwargs):
        if args[0] == "git":
            return "main\n" if args[1] == "branch" else "" if args[1] == "status" else HEAD + "\n"
        return "postgresql+psycopg://test:test@localhost:5433/harness_test_main\n"

    def popen(args, **kwargs):
        launches.append((args, kwargs))
        # Verify the passed descriptor is an actual open lock file, not a fabricated fd.
        assert Path(f"/dev/fd/{kwargs['pass_fds'][0]}").exists()
        return SimpleNamespace(pid=12345, wait=lambda: 7)

    monkeypatch.setattr(module.subprocess, "check_output", check_output)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    assert module.main() == 7
    args, kwargs = launches[0]
    assert kwargs["start_new_session"] is True and len(kwargs["pass_fds"]) == 1
    assert kwargs["env"]["DATABASE_URL_TEST"].endswith("localhost:5433/harness_test_main")
    receipt = json.loads((tmp_path / ".cache/sports-harness/test-state/test-harness_test_main.json").read_text())
    assert receipt["exit_code"] == 7 and receipt["scope"] == ["tests/test_one.py"]


def test_window_is_rechecked_after_build_before_stopping_services(release, monkeypatch):
    checks = []

    def game_window(mode):
        checks.append(mode)
        if len(checks) == 2:
            raise RuntimeError("Deploy window closed while building")
        return {"blocked": False, "nfl_blocked": False}

    monkeypatch.setattr(release.module, "game_window", game_window)
    with pytest.raises(RuntimeError, match="window closed"):
        release.module.deploy("full")
    assert len(checks) == 2
    assert not any("stop" in call for call in release.calls)


def test_roll_back_failure_is_recorded_without_claiming_old_apps_restored(release, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("health unavailable")

    monkeypatch.setattr(release.module, "wait_healthy", unavailable)
    with pytest.raises(RuntimeError):
        release.module.deploy("full")
    receipt = release_receipt(release)
    assert receipt["status"] == "rollback-failed"
    assert receipt["original_error"] == "RuntimeError"
    assert receipt["rollback_error"] == "RuntimeError"


@pytest.mark.parametrize("field,value", [("pytest_addopts", "-k one_test"),
                                          ("pytest_plugins", "skip_everything")])
def test_hidden_pytest_options_or_plugins_cannot_authorize_release(release, field, value):
    release.receipt[field] = value
    release.test_receipt.write_text(json.dumps(release.receipt))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")
    assert not any("build" in call or "stop" in call for call in release.calls)


@pytest.mark.parametrize("field", ["pytest_addopts", "pytest_plugins"])
def test_legacy_receipt_without_filter_metadata_cannot_authorize_release(release, field):
    del release.receipt[field]
    release.test_receipt.write_text(json.dumps(release.receipt))
    with pytest.raises(RuntimeError, match="receipt"):
        release.module.deploy("app")
    assert not any("build" in call or "stop" in call for call in release.calls)


@pytest.mark.parametrize("failure", ["restore-file", "rollback-up"])
def test_rollback_operation_failure_has_explicit_failed_status(release, monkeypatch, failure):
    release.failure = "health"
    if failure == "restore-file":
        original = release.module.atomic_copy

        def copy(source, dest):
            if source.name == "previous-compose.omarchy.yml":
                raise OSError("injected rollback file failure")
            return original(source, dest)

        monkeypatch.setattr(release.module, "atomic_copy", copy)
    else:
        original = release.module.run
        ups = []

        def run(args, **kwargs):
            if "up" in args:
                ups.append(True)
                if len(ups) == 2:
                    raise subprocess.CalledProcessError(1, args)
            return original(args, **kwargs)

        monkeypatch.setattr(release.module, "run", run)
    with pytest.raises((RuntimeError, OSError, subprocess.CalledProcessError)):
        release.module.deploy("full")
    assert release_receipt(release)["status"] == "rollback-failed"


@pytest.fixture
def readiness(monkeypatch, tmp_path):
    module = load_script("release-omarchy")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(module, "RUNTIME", runtime)
    state = SimpleNamespace(module=module, mode=None, calls=[], queries=[])
    names = module.APPS + ["app-ws", "postgres"]
    config = {"services": {name: {"image": f"sports-release/app:{SHA}",
                                  "environment": {"BUILD_SHA": SHA}}
                           for name in names}}
    times = iter([0, 0, 10, 10])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module, "health", lambda: {"build": SHA, "status": "ok"})

    def run(args, **kwargs):
        args = [str(arg) for arg in args]
        state.calls.append(args)
        if "config" in args:
            return json.dumps(config)
        if "ps" in args:
            if "-q" in args:
                return args[-1] + "-container"
            return json.dumps([{"Service": name, "State": "running", "Health": "healthy"}
                               for name in names])
        if args[:2] == ["docker", "inspect"]:
            service = args[-1].removesuffix("-container")
            image_tag, image_id, stamp = f"sports-release/app:{SHA}", "sha256:new-image", SHA
            if service == "app-run":
                if state.mode == "wrong-stamp":
                    stamp = OLD
                if state.mode in ("wrong-image", "wrong-tag"):
                    image_id = "sha256:old-image"
                if state.mode == "wrong-tag":
                    image_tag = f"sports-migration/app:{OLD}"
            return json.dumps([{"Image": image_id, "Config": {
                "Image": image_tag, "Env": [f"BUILD_SHA={stamp}"]}}])
        if args[:3] == ["docker", "image", "inspect"]:
            return "sha256:old-image" if OLD in args[3] else "sha256:new-image"
        raise AssertionError(f"Unmocked operation: {args}")

    def sql(query):
        state.queries.append(query)
        if state.mode == "old-tick":
            return json.dumps({"build_sha": OLD, "status": "ok"})
        if state.mode == "quiet":
            # Real recorder completes skipped heartbeats in quiet hours; latest OK can be old.
            stamp = OLD if "status='ok'" in query.replace(" ", "") else SHA
            return json.dumps({"build_sha": stamp, "status": "skipped" if stamp == SHA else "ok"})
        return json.dumps({"build_sha": SHA, "status": "ok"})

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module, "sql", sql)
    return state


@pytest.mark.parametrize("mismatch", ["wrong-stamp", "wrong-image", "wrong-tag", "old-tick"])
def test_ready_requires_new_container_image_stamp_and_recorder_evidence(readiness, mismatch):
    readiness.mode = mismatch
    with pytest.raises(RuntimeError):
        readiness.module.wait_healthy(SHA, readiness.module.APPS, timeout=1)


def test_ready_accepts_a_new_completed_quiet_hour_heartbeat(readiness):
    readiness.mode = "quiet"
    assert readiness.module.wait_healthy(SHA, readiness.module.APPS, timeout=1)["build"] == SHA


def test_changed_manual_aliases_are_seeded_or_require_full_release(release):
    release.touched = ["harness/matching/aliases_manual.yaml"]
    try:
        release.module.deploy("app")
    except RuntimeError as error:
        assert "Full release" in str(error)
        return
    assert any(call[-1:] == ["seed-teams"] for call in release.calls)


def test_health_can_read_a_known_build_from_the_expected_stale_503(monkeypatch):
    module = load_script("release-omarchy")
    payload = {"build": OLD, "status": "stale"}

    def stale(*args, **kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1:8180/healthz", 503,
                                     "stale", {}, io.BytesIO(json.dumps(payload).encode()))

    monkeypatch.setattr(module.urllib.request, "urlopen", stale)
    assert module.health() == payload


def test_capacity_budget_cannot_silently_revert_to_nas_setting(release):
    config = copy.deepcopy(release.before)
    for name in release.module.APPS + ["app-ws"]:
        config["services"][name]["environment"]["DB_BUDGET_GB"] = "2000"
    with pytest.raises(RuntimeError):
        release.module.validate_config(config)


def test_full_rollback_preserves_older_ws_stamp_from_prior_app_only_release(release):
    ws = release.before["services"]["app-ws"]
    ws["environment"]["BUILD_SHA"] = "older-ws"
    ws["image"] = "sports-migration/app:older-ws"
    (release.runtime / "compose.omarchy.yml").write_text(json.dumps(release.before))
    release.failure = "health"
    with pytest.raises(RuntimeError):
        release.module.deploy("full")
    stamp, services, kwargs = release.health_calls[-1]
    assert stamp == OLD and "app-ws" in services
    assert kwargs["expected"]["app-ws"]["build"] == "older-ws"
    assert kwargs["expected"]["app-run"]["build"] == OLD


def test_termination_signals_enter_python_unwinding_before_deployment(release, monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler))
    monkeypatch.setattr(release.module.sys, "platform", "linux")
    monkeypatch.setattr(release.module.sys, "argv", ["release-omarchy.py", "--mode", "full"])

    def deploy(mode, plan=False):
        assert mode == "full" and plan is False
        for sig in (signal.SIGTERM, signal.SIGHUP):
            assert callable(handlers.get(sig)), "Termination must unwind through rollback"
            with pytest.raises((SystemExit, KeyboardInterrupt)):
                handlers[sig](sig, None)

    monkeypatch.setattr(release.module, "deploy", deploy)
    release.module.main()


def test_mounted_backup_script_change_cannot_be_silently_ignored_by_full_release(release):
    release.touched = ["deploy/backup/loop.sh"]
    with pytest.raises(RuntimeError, match="[Ii]nfrastructure|separate reviewed"):
        release.module.deploy("full")
    assert not any("stop" in call for call in release.calls)


def test_seed_teams_network_failure_preserves_original_warning_only_behavior(release):
    release.failure = "seed-teams"
    release.module.deploy("full")
    assert release.failed
    assert release_receipt(release)["status"] == "healthy"


def test_full_rollback_reactivates_previous_variants_before_restarting_writers(release):
    release.touched = ["harness/variants/sharp_direct.yaml"]
    release.failure = "health"
    with pytest.raises(RuntimeError):
        release.module.deploy("full")
    registrations = [i for i, call in enumerate(release.calls)
                     if call[-2:] == ["variants", "register"]]
    restarts = [i for i, call in enumerate(release.calls) if "up" in call]
    assert len(registrations) == 2, "Restoring images alone leaves candidate variants active in the DB"
    assert registrations[-1] < restarts[-1]
    rollback = release.calls[registrations[-1]]
    assert "candidate-override.json" not in " ".join(rollback)
