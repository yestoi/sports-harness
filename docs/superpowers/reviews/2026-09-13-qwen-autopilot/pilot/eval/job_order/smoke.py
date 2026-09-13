from solution import ready_jobs

jobs = [dict(id='a', priority=1, submitted=0, depends_on=[]),
        dict(id='b', priority=9, submitted=1, depends_on=['a'])]
assert ready_jobs(jobs, set()) == ['a']
assert ready_jobs(jobs, {'a'}) == ['b']
