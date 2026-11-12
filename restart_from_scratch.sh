#!/bin/sh
# Discard generated outputs and traffic downloads, then run scripts 01–10.
# Pagination checkpoints go with the traffic tree, so the next download
# starts at offset 0. The asthma export and pinned geography files stay.
set -e

cd "$(dirname "$0")"

refuse_if_symlink() {
    if [ -L "$1" ]; then
        echo "$1 is a symlink; refusing to remove it" >&2
        exit 2
    fi
}

refuse_if_symlink outputs
refuse_if_symlink data
refuse_if_symlink data/raw
refuse_if_symlink data/raw/traffic

if [ -e outputs ]; then
    rm -rf outputs
    echo "removed outputs"
fi

if [ -e data/raw/traffic ]; then
    rm -rf data/raw/traffic
    echo "removed data/raw/traffic"
fi

echo "restarting from scratch"
exec ./run_all.sh
