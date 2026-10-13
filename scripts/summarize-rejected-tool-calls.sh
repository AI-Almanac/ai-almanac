#!/usr/bin/env bash
# summarize-rejected-tool-calls — print, as Markdown, the tool calls a Claude
# review run tried but was not allowed to make.
#
# Usage: summarize-rejected-tool-calls.sh <claude-execution-output.json>
#
# The review workflows append this to the job summary. Each rejected call
# costs a turn, so this list is where to look when a review hits its turn
# limit. Only the tool name and its command or path are printed, never results.
set -euo pipefail

execution_file="${1:?usage: $0 <claude-execution-output.json>}"

if [ ! -f "$execution_file" ]; then
  echo "No execution output at $execution_file; the review did not start."
  exit 0
fi

rejected=$(jq -r '
  .[] | select(.type == "result") | .permission_denials[]?
  | "\(.tool_name): \(.tool_input.command // .tool_input.file_path // .tool_input | tostring | gsub("\n"; " ") | .[0:200])"
' "$execution_file")

echo "### Rejected tool calls"
if [ -z "$rejected" ]; then
  echo "None."
else
  echo '```text'
  echo "$rejected"
  echo '```'
fi
