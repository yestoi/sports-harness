from solution import utf8_tail

assert utf8_tail([b'A\xe2', b'\x82\xacB'], 4) == '€B'
assert utf8_tail([b'abc'], 2) == 'bc'
assert utf8_tail([], 0) == ''
