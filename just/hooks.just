# Mutating pre-commit hooks that automatically restage modified files.
# This was written by Codex based on https://github.com/hallettj/git-format-staged/
#
# ----- >8 ----- ORIGINAL LICENSE ----- >8 -----
# Copyright 2018 Jesse Hallett
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ----- >8 ---------------------------- >8 -----

set shell := ["bash", "-euo", "pipefail", "-c"]

[doc("Install versioned git hooks in this checkout")]
[no-cd]
install-hooks:
    @if ! hooks_path="$(git config --local --get core.hooksPath)"; then \
      git config --local core.hooksPath .githooks; \
      echo "Configured core.hooksPath=.githooks"; \
    else \
      echo "Using existing core.hooksPath=$hooks_path"; \
    fi

[doc("Run staged formatting and then check-only pre-commit hooks")]
[no-cd]
pre-commit:
    @format_staged() { \
      local path="$1"; \
      local kind="$2"; \
      local tmpdir mode blob diff_status; \
      tmpdir="$(mktemp -d)"; \
      git show ":$path" > "$tmpdir/original"; \
      case "$kind" in \
        python) \
          UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-sync ruff format --stdin-filename "$path" - < "$tmpdir/original" > "$tmpdir/formatted"; \
          ;; \
        toml) \
          UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-sync tombi format --stdin-filename "$path" - < "$tmpdir/original" > "$tmpdir/formatted"; \
          ;; \
        *) \
          echo "unsupported formatter kind: $kind" >&2; \
          rm -rf "$tmpdir"; \
          return 1; \
          ;; \
      esac; \
      if cmp -s "$tmpdir/original" "$tmpdir/formatted"; then \
        rm -rf "$tmpdir"; \
        return 0; \
      fi; \
      mode="$(git ls-files --stage -- "$path" | awk 'NR == 1 { print $1 }')"; \
      blob="$(git hash-object -w --stdin < "$tmpdir/formatted")"; \
      git update-index --cacheinfo "$mode,$blob,$path"; \
      if [ -e "$path" ]; then \
        if cmp -s "$path" "$tmpdir/original"; then \
          cp "$tmpdir/formatted" "$path"; \
        else \
          if diff -u --label="a/$path" --label="b/$path" "$tmpdir/original" "$tmpdir/formatted" > "$tmpdir/patch"; then \
            :; \
          else \
            diff_status="$?"; \
            if [ "$diff_status" -gt 1 ]; then \
              echo "diff failed for $path" >&2; \
              rm -rf "$tmpdir"; \
              return 1; \
            fi; \
            if ! git apply --recount < "$tmpdir/patch"; then \
              echo "warning: formatted staged $path, but could not update the working tree" >&2; \
            fi; \
          fi; \
        fi; \
      fi; \
      echo "formatted staged $path" >&2; \
      rm -rf "$tmpdir"; \
    }; \
    staged_files=(); \
    while IFS= read -r -d '' path; do \
      staged_files+=("$path"); \
      case "$path" in \
        *.py) \
          format_staged "$path" python; \
          ;; \
        *.toml) \
          if [ "$path" != "uv.lock" ]; then \
            format_staged "$path" toml; \
          fi; \
          ;; \
      esac; \
    done < <(git diff --cached --name-only -z --diff-filter=ACMR); \
    if ((${#staged_files[@]} > 0)); then \
      UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-sync pre-commit run --files "${staged_files[@]}"; \
    fi

[doc("Check for trailing whitespace in files")]
[no-cd]
[positional-arguments]
check-trailing-whitespace *files:
    @failed=0; \
    for file in "$@"; do \
      sed -i .bak -e 's/[ 	]*$//g' "$file"; \
      rm "$file.bak"; \
    done

[doc("Require exactly one final newline in files")]
[no-cd]
[positional-arguments]
check-end-of-file *files:
    @failed=0; \
    for file in "$@"; do \
      if [ ! -s "$file" ]; then \
        continue; \
      fi; \
      last_one="$(tail -c 1 "$file" | od -An -tx1 | tr -d ' \n')"; \
      last_two="$(tail -c 2 "$file" | od -An -tx1 | tr -d ' \n')"; \
      last_four="$(tail -c 4 "$file" | od -An -tx1 | tr -d ' \n')"; \
      if [ "$last_one" != "0a" ] || [ "$last_two" = "0a0a" ] || [ "$last_four" = "0d0a0d0a" ]; then \
        echo "$file: file must end with exactly one newline" >&2; \
        failed=1; \
      fi; \
    done; \
    exit "$failed"
