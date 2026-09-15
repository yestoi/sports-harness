from solution import eligible

sha = 'a' * 40
receipt = dict(run_id='r1', origin='trusted-runner', head=sha, head_after=sha,
               profile='unit-v1', exit_code=0, scope=[], dirty_before='', dirty_after='',
               environment={'addopts': '', 'plugins': ''})
assert eligible(receipt, sha, 'unit-v1') is True
receipt['exit_code'] = False
assert eligible(receipt, sha, 'unit-v1') is False
