export UV_FROZEN := "1"

mod docker 'just/docker.just'
mod hooks 'just/hooks.just'

@_help:
    just --list --list-submodules

[doc("Setup a new repo")]
[group("Development Environment")]
setup:
    @just hooks::install-hooks
    uv sync --all-extras --all-groups --all-packages

[doc("Lint files, defaulting to all")]
[group("Development Tasks")]
analyze *FILES:
    uv run ruff check {{ FILES }}
    uv run pyrefly check .  # always analyse complete context
    uv run ty check .  # always analyse complete context
    dprint check --allow-no-files {{ FILES }}

[doc("Format files (defaults to all files)")]
[group("Development Tasks")]
format *FILES:
    dprint fmt --allow-no-files {{ FILES }}
    just --fmt --unstable

[doc("Run tests")]
[group("Development Tasks")]
test *ARGS:
    uv run coverage run -m pytest {{ ARGS }}
    uv run coverage report

[doc("Build docker images")]
[group("Development Tasks")]
docker-build:
    docker build -t github-webhook:local --build-arg APPLICATION=github-webhook --target=webapp -f packaging/docker/Dockerfile.python .
    docker build -t agent-consumer:local --build-arg APPLICATION=agent-consumer --target=consumer -f packaging/docker/Dockerfile.python .
