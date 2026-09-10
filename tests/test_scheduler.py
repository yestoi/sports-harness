"""The scheduler's job wiring. The settler gets its own slot beside the recorder's tick, so a
600 s settlement batch can never delay a 30 s heartbeat (arch review §3.4)."""

from types import SimpleNamespace

from harness.scheduler import build_scheduler


def _jobs(sched):
    return {job.id: job for job in sched.get_jobs()}


def test_build_scheduler_registers_the_settle_job():
    recorder = SimpleNamespace(maybe_tick=lambda: None)
    settler = SimpleNamespace(run=lambda: None)

    sched = build_scheduler(recorder, 30, settler=settler, settle_period_s=900)
    jobs = _jobs(sched)

    assert set(jobs) == {"maybe_tick", "settle"}
    settle = jobs["settle"]
    assert settle.func is settler.run
    assert settle.trigger.interval.total_seconds() == 900
    assert settle.max_instances == 1
    assert settle.coalesce is True
    assert settle.misfire_grace_time == 300
    # The tick keeps its own slot and its own cadence.
    assert jobs["maybe_tick"].trigger.interval.total_seconds() == 30


def test_build_scheduler_without_a_settler_is_unchanged():
    recorder = SimpleNamespace(maybe_tick=lambda: None)

    assert set(_jobs(build_scheduler(recorder, 30))) == {"maybe_tick"}


def _recorder():
    return SimpleNamespace(maybe_tick=lambda: None)


def test_the_futures_job_runs_on_tuesdays_at_nine_central():
    """Ruling B-M6: the scheduler is built with timezone="UTC", so the trigger carries its own
    or the Tuesday 09:30 CT duty finds a job that ran at 04:00 local."""
    from harness.scheduler import build_scheduler

    # No `shutdown()`: `build_scheduler` never calls `.start()`, and APScheduler raises
    # `SchedulerNotRunningError` on a stopped scheduler. The file's two existing tests do the
    # same thing for the same reason.
    sched = build_scheduler(_recorder(), heartbeat_s=30, futures=lambda: None)
    job = sched.get_job("futures_snapshot")
    assert job is not None
    assert str(job.trigger.timezone) == "America/Chicago"
    assert "tue" in str(job.trigger)
    assert "hour='9'" in str(job.trigger) and "minute='0'" in str(job.trigger)


def test_no_futures_job_without_a_callable():
    from harness.scheduler import build_scheduler

    sched = build_scheduler(_recorder(), heartbeat_s=30)
    assert sched.get_job("futures_snapshot") is None
