#!/usr/bin/env bash
# Headless pi review of the staged changes.
#
# Security gate. Blocks the commit when pi reports a security issue OR when
# the verdict cannot be verified (fail closed). The prompt instructs pi to end
# its reply with exactly one marker line:
#   - "SECURITY_BLOCK: <desc>"  -> security issue found -> exit 1 (block)
#   - "SECURITY_OK"             -> no security issue     -> exit 0 (allow)
# Non-security findings (bugs, perf, style) are reported but never block.
set -uo pipefail

if ! command -v pi >/dev/null 2>&1; then
  echo "pi-review: 'pi' not found on PATH; skipping review" >&2
  exit 0
fi

output="$(pi -p --no-session --provider ollama --model deepseek-v4-pro:cloud \
  "Review the staged changes (git diff --cached) in this repository. \
Focus on bugs, logic errors, security issues, and error-handling gaps. \
Report concrete issues with file:line references. Be concise. \
End your reply with exactly one final line, nothing after it: \
either 'SECURITY_BLOCK: <one-line description>' if you found any SECURITY \
issue, or 'SECURITY_OK' if there are none." 2>&1)"

printf '%s\n' "$output"

# Only the final non-empty line is the verdict marker; prose above it may
# legitimately discuss the marker and must not trigger a false block.
last_line="$(printf '%s\n' "$output" | grep -v '^[[:space:]]*$' | tail -1)"

if [[ "$last_line" == SECURITY_BLOCK:* ]]; then
  echo "pi-review: security issue(s) found — blocking commit" >&2
  exit 1
fi

if [[ "$last_line" != "SECURITY_OK" ]]; then
  # No recognized verdict (pi error, truncation, malformed marker). Fail
  # closed: a security gate must not silently allow a commit it could not
  # verify.
  echo "pi-review: no verdict marker detected — blocking commit (fail closed)" >&2
  exit 1
fi

exit 0
