export UV_FROZEN := "1"

@_help:
    just --list

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
