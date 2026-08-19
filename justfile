export UV_FROZEN := "1"

@_help:
    just --list

# Format all files
format:
    uv run pre-commit run --all-files ruff-format
    uv run pre-commit run --all-files tombi-format
    just --fmt --unstable
