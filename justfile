export UV_FROZEN := "1"

mod hooks 'just/hooks.just'

@_help:
    just --list --list-submodules

# Setup a new repo
setup:
    @just hooks::install-hooks
    uv sync --all-extras --all-groups --all-packages

# Lint files, defaulting to all
check *FILES:
    uv run ruff check {{ FILES }}

# Format files (defaults to all files)
format *FILES:
    dprint fmt --allow-no-files {{ FILES }}
    just --fmt --unstable

# Build docker images
docker-build:
    docker build -t github-webhook:local --build-arg APPLICATION=github-webhook -f packaging/docker/Dockerfile.python .
