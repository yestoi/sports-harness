from solution import parse_duration

assert parse_duration('1h 2m 3s 4ms') == 3723004
assert parse_duration('0s') == 0
try:
    parse_duration('1m1h')
except ValueError:
    pass
else:
    raise AssertionError('unit order must be checked')
