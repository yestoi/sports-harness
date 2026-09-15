"""The research worker's loop: the two switches, the pass registry, and what it reports."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from harness.research import worker as worker_module
from harness.research.worker import MAX_CONCURRENT_CALLS, POLL_S, ResearchWorker, register_pass

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def clean_registry():
    """Isolate the registry, both halves.

    `PASS_MODULES` is cleared too, not only `PASSES`. `run_once` calls `load_passes()`, which
    imports every name in `PASS_MODULES`; once T15 and T18 populate that list, a module not yet
    imported in this pytest session would run its module-level `register_pass` against the
    freshly cleared `PASSES` and this file's assertions would depend on test file order.
    """
    saved_passes = list(worker_module.PASSES)
    saved_modules = list(worker_module.PASS_MODULES)
    worker_module.PASSES.clear()
    worker_module.PASS_MODULES.clear()
    yield
    worker_module.PASSES.clear()
    worker_module.PASSES.extend(saved_passes)
    worker_module.PASS_MODULES.clear()
    worker_module.PASS_MODULES.extend(saved_modules)


def _worker(db_session, settings):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return ResearchWorker(settings, factory, clock=lambda: NOW)


def test_the_worker_is_dormant_without_the_key(db_session, env_settings, clean_registry):
    register_pass("boom", lambda *_: (_ for _ in ()).throw(AssertionError("ran")))
    result = _worker(db_session, env_settings).run_once()
    assert result == {"status": "dormant", "reason": "no key", "passes": []}


def test_the_worker_is_disabled_by_its_setting(db_session, env_settings, tmp_path,
                                               clean_registry):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key,
                                               "research_worker_enabled": False})
    register_pass("boom", lambda *_: (_ for _ in ()).throw(AssertionError("ran")))
    assert _worker(db_session, settings).run_once() == \
        {"status": "disabled", "reason": "research_worker_enabled is false", "passes": []}


def test_a_pass_runs_and_its_counts_are_reported(db_session, env_settings, tmp_path,
                                                 clean_registry):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    seen = {}

    def one(session, now, s):
        seen["now"] = now
        return {"decided": 3}

    register_pass("one", one)
    result = _worker(db_session, settings).run_once()
    assert seen["now"] == NOW
    assert result["status"] == "ok"
    assert result["passes"] == [{"name": "one", "counts": {"decided": 3}, "error": None}]


def test_a_raising_pass_does_not_stop_the_next_one(db_session, env_settings, tmp_path,
                                                   clean_registry):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    register_pass("bad", lambda *_: (_ for _ in ()).throw(ValueError("nope")))
    register_pass("good", lambda *_: {"ok": 1})

    result = _worker(db_session, settings).run_once()
    assert result["status"] == "degraded"
    assert result["passes"][0]["error"] == "ValueError"
    assert result["passes"][1]["counts"] == {"ok": 1}


def test_a_pass_error_is_a_class_name_never_the_message(db_session, env_settings, tmp_path,
                                                        clean_registry):
    """The same rule the dashboard's `error` column follows: an exception message can carry SQL
    text and row content, and this string reaches a log the operator reads."""
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    register_pass("bad", lambda *_: (_ for _ in ()).throw(ValueError("select * from secrets")))
    result = _worker(db_session, settings).run_once()
    assert result["passes"][0]["error"] == "ValueError"


def test_registering_a_name_twice_is_a_no_op(clean_registry):
    register_pass("dup", lambda *_: {})
    register_pass("dup", lambda *_: {})
    assert [name for name, _ in worker_module.PASSES] == ["dup"]


def test_the_loop_constants():
    assert POLL_S == 30
    assert MAX_CONCURRENT_CALLS == 2


def test_the_pass_module_registry_starts_empty_and_is_a_list_of_strings():
    """T15 and T18 each append one module name. Never an import here: a module that does not
    exist yet must not be able to break this one."""
    assert isinstance(worker_module.PASS_MODULES, list)
    assert all(isinstance(name, str) for name in worker_module.PASS_MODULES)


# --- reconciliation at worker start (fix 74) -------------------------------------------------

def test_run_forever_releases_a_stale_reservation_and_writes_one_event(
        db_session, env_settings, tmp_path, clean_registry):
    from sqlalchemy import text
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    db_session.execute(text(
        "insert into research_spend (day, kind, model, usd_reserved, usd, calls, "
        "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, searches) "
        "values ('2026-09-15', 'veto', 'claude-opus-5', 1.2972, 14.9630, 3, 0, 0, 0, 0, 0)"))
    db_session.commit()
    register_pass("nop", lambda *_: {})

    worker = _worker(db_session, settings)

    def fake_sleep(_):
        worker.stop()

    worker._sleep = fake_sleep
    worker.run_forever()

    row = db_session.execute(text(
        "select usd_reserved from research_spend where model = 'claude-opus-5'")).scalar()
    assert row == 0
    events = db_session.execute(text(
        "select kind, summary, ref from operator_events")).all()
    assert len(events) == 1
    assert events[0].kind == "research_spend_released"
    # Exact text, not a substring (review round 1 Minor 1): `telemetry.sanitize_reason` strips
    # any character outside `[\w \-.,:/()]`, so a `$` in the f-string would be silently
    # dropped from what is actually stored, and a substring check on the digits alone would
    # not catch that.
    assert events[0].summary == \
        "released USD 1.2972 of stale research reservations at worker start (1 rows)"
    assert events[0].ref["rows"] == [["2026-09-15", "veto", "claude-opus-5", "1.2972"]]


def test_run_forever_writes_no_event_when_nothing_is_stale(
        db_session, env_settings, tmp_path, clean_registry):
    from sqlalchemy import text
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    settings = env_settings.model_copy(update={"anthropic_api_key_file": key})
    register_pass("nop", lambda *_: {})

    worker = _worker(db_session, settings)

    def fake_sleep(_):
        worker.stop()

    worker._sleep = fake_sleep
    worker.run_forever()

    events = db_session.execute(text("select kind from operator_events")).all()
    assert events == []
