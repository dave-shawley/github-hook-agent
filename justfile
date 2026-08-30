export UV_FROZEN := "1"

mod hooks 'just/hooks.just'

@_help:
    just --list --list-submodules

# Setup a new repo
setup:
    @just hooks::install-hooks
    uv sync --all-extras --all-groups --all-packages

# Lint files, defaulting to all
analyze *FILES:
    uv run ruff check {{ FILES }}
    uv run pyrefly check .  # always analyse complete context
    uv run ty check .  # always analyse complete context
    dprint check --allow-no-files {{ FILES }}

# Format files (defaults to all files)
format *FILES:
    dprint fmt --allow-no-files {{ FILES }}
    just --fmt --unstable

# Run tests
test *ARGS:
    uv run coverage run -m pytest {{ ARGS }}
    uv run coverage report

# Build docker images
docker-build:
    docker build -t github-webhook:local --build-arg APPLICATION=github-webhook --target=webapp -f packaging/docker/Dockerfile.python .
    docker build -t agent-consumer:local --build-arg APPLICATION=agent-consumer --target=consumer -f packaging/docker/Dockerfile.python .
