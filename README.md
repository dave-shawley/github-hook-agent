# AMQP processing of GitHub hooks

This monorepo is an experiment in repo design for processing GitHub webhook notifications using agents running as AMQP consumers. Using AMQP consumers to control agentic processing has some interesting properties that I think is worth exploring. Using a monorepo to provide clear context to agents is also something that I have been exploring. This project mashes the two of them together. It might be another dead repo that I archive away or it may turn out to be something more interesting.

## Repository structure

- `apps/`: contains application code that is built into a docker image
- `libraries/`: contains shared libraries and utilities
- `dev/`: contains development configuration
- `packaging/`: contains build scripts and configuration for artifacts such as docker images
- `justfile` is the primary entrypoint for running build and development tasks

### The just command runner

Install `just` from https://just.systems/ if you do not have it installed. It is a great replacement for `make` and provides a consistent progressive discovery mechanism that makes it easy to discover and run tasks. You can start by running
`just setup` to install dependencies and set up the environment.

The `justfile` is where you want to start when you need to do something. The default recipe lists the available recipes and tasks that you can run. You SHOULD NOT need to run commands directly unless you are adding a new repository or dependencies. Check with what is available before you run other commands. My intention is that the `justfile` should be the catalog of tasks that you need to run during development.

### Pre-commit hooks, formatting, etc

`just setup` will install the git pre-commit hooks that ensure that your code matches expectations. The hooks use the `pre-commit` utility and the [git-format-staged](https://github.com/hallettj/git-format-staged/) system to automatically pull reformatted code into the commit without manual intervention. This is a quality of life improvement that I hope will save you time and frustration.

Speaking of code formatting, use `just format {file}` to format a file. Don't try to guess what formatting rules are in place - `just format` will handle it for you. I use the [dprint](https://dprint.dev/) utility for cross language formatting. It uses [ruff](https://github.com/astral-sh/ruff) under the hood for python, [tombi](https://tombi-toml.github.io/tombi/docs/) for TOML, and other formatters for other languages. You do not need to understand the details, run `just format` and be done with it. If you are using an editor, the `ruff` rules in the top-level `pyproject.toml` file are what governs the Python codebase.
