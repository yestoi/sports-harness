"""The release tree: a content hash of a revision with the loop's bookkeeping left out.

`docs/superpowers/autopilot/` (journal, state, roadmap, evidence) changes after nearly every
merge, and nothing the suite runs reads it (tests/test_release_tree.py guards that), so a
full-suite receipt taken on the merged code still vouches for main after the journal commit.
The hash covers every other `git ls-tree -r` entry: mode, type, blob id and path.
"""
import hashlib
import subprocess

EXCLUDED = ('docs/superpowers/autopilot/',)


def release_tree(rev='HEAD', run=subprocess.check_output):
    listing = run(['git', 'ls-tree', '-r', rev], text=True)
    kept = [line for line in listing.splitlines() if not line.rpartition('\t')[2].startswith(EXCLUDED)]
    return hashlib.sha256('\n'.join(kept).encode()).hexdigest()
