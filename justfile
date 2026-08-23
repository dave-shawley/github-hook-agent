export UV_FROZEN := "1"

mod hooks 'just/hooks.just'

@_help:
    just --list --list-submodules

# Setup a new repo
setup:
    @just hooks::install-hooks
    uv sync --all-extras --all-groups

# Lint files, defaulting to all
check *FILES:
    uv run ruff check {{ FILES }}

# Format all files
format:
    uv run pre-commit run --all-files ruff-format
    uv run pre-commit run --all-files tombi-format
    just --fmt --unstable

# Build docker image
docker-build name="github-hook-agent:local":
    docker build -t {{ name }} .
