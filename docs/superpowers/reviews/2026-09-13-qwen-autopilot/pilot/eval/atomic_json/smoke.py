import tempfile
from pathlib import Path
from solution import write_snapshot

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'sample.json'
    assert write_snapshot(path, {'b': 2, 'a': 1}) is None
    assert path.read_text(encoding='utf-8') == '{\n  "a": 1,\n  "b": 2\n}\n'
