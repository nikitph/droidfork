#!/bin/sh
set -eu
target=.lab/android_world
rev=55ddaf5edc6f6c25673aed343907d5b9dca66f84
mkdir -p .lab
if [ ! -d "$target/.git" ]; then
    git init -q "$target"
    git -C "$target" fetch --depth 1 https://github.com/google-research/android_world.git "$rev"
    git -C "$target" checkout -q --detach FETCH_HEAD
fi
test "$(git -C "$target" rev-parse HEAD)" = "$rev"
uv venv --allow-existing --python 3.11 .lab/awenv
uv pip install -p .lab/awenv/bin/python "$target" Pillow
