# Task job_order: deterministic ready jobs

Implement `ready_jobs(jobs, completed)` in `solution.py` using only Python's standard library. This is a pure toy scheduling exercise, not a production controller. Do not edit the smoke.

`jobs` must be a list of dicts, each with `id` (nonempty string), `priority` (integer 0 through 9, excluding bool), `submitted` (nonnegative integer, excluding bool), and `depends_on` (list of distinct job-ID strings). Job IDs must be unique. Dependencies must name existing jobs and cannot reference the job itself. Extra dict keys are allowed. The dependency graph must be acyclic, including already completed jobs.

`completed` must be a set of IDs present in jobs. Return a new list of IDs of uncompleted jobs whose direct dependencies are all in completed. Order by priority descending, submitted ascending, then ID in Python string order ascending. Do not infer additional completions or topologically schedule later work in the same call. Empty jobs plus empty completed yields an empty list. Completed is an input fact; it need not be dependency-closed.

Raise ValueError for any malformed input or graph, including missing fields and type errors. Validate every job even if completed or currently blocked. Do not mutate inputs. Inputs are ordinary finite Python values; performance should be reasonable for hundreds of jobs.
