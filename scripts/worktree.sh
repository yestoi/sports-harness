#!/usr/bin/env bash
# Create or remove an implementer worktree with the shared .venv linked in.
# Usage: scripts/worktree.sh add BRANCH [BASE]   -> prints the worktree path
#        scripts/worktree.sh rm  BRANCH          -> removes the worktree (branch kept)
set -eu
ROOT=$(git rev-parse --show-toplevel)
WT_DIR="$ROOT/../sports-wt"
case "${1:-}" in
  add)
    BR=$2; BASE=${3:-main}; P="$WT_DIR/$BR"
    mkdir -p "$WT_DIR"
    if git show-ref --verify --quiet "refs/heads/$BR"; then git worktree add "$P" "$BR" >/dev/null
    else git worktree add -b "$BR" "$P" "$BASE" >/dev/null; fi
    ln -sfn "$ROOT/.venv" "$P/.venv"
    echo "$P" ;;
  rm)
    BR=$2; git worktree remove --force "$WT_DIR/$BR"; git worktree prune ;;
  *) echo "usage: $0 add BRANCH [BASE] | rm BRANCH" >&2; exit 2 ;;
esac
