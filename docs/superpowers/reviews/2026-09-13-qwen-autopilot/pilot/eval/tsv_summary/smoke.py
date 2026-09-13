from solution import summarize

assert summarize([{'status': 'done', 'duration_ms': 2},
                  {'status': 'done', 'duration_ms': 3}]) == (
    'status\tcount\ttotal_ms\tavg_ms\ndone\t2\t5\t3\n')
