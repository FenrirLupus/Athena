#!/bin/bash
# Release gate: every suite, plus the permissions manager's own.
#
# A suite that fails must say why. Capturing only the summary line meant a timeout, a crash or an import error
# produced an empty line, and an empty line counted as a failure with no reason printed anywhere — a gate that says
# FAIL while every visible line says PASS is worse than no gate, because it teaches people to ignore it.
cd "$(dirname "$0")/.." || exit 1
PY=../.venv/bin/python3
fail=0
failed_suites=()
logdir=$(mktemp -d)

for t in version_check identity_check permissions_check approval_check self_modify_check scope_check install_request_check \
         plugin_install_check tool_install_check box_request_check security_check \
         tools_skills_plugins toolpacks_check sandbox_and_budget_check outcomes_check subagents_check \
         instruction_docs_check \
         plugin_isolation_check plugins_safety_check admission_check idle_sleep waking_state \
         routing_check lifecycle_check config_parity lane_plugins_check updater_check ledger_view_check context_window_check comment_style_check publish_safety_check web_assets_check timefmt_check pages_build_check metrics_check memory_check curator_flow_check curator_concurrency_check home_capabilities_check curator_safety_check vault_tiering_check sql_tools_check \
         system_tools_check references_check repo_intake_check trace_check; do
  log="$logdir/$t.log"
  timeout 600 "$PY" "tests/$t.py" >"$log" 2>&1
  code=$?
  line=$(grep -aE '^(RESULT|SYSTEM)|— RESULT|^ *[0-9]+/[0-9]+' "$log" | tail -1)
  verdict="${line:-NO RESULT}"

  # good only when it exited 0, printed a verdict, and that verdict is not a failure
  ok=1
  [ "$code" -ne 0 ] && ok=0
  [ -z "$line" ] && ok=0
  case "$line" in *FAIL*) ok=0 ;; esac

  if [ "$ok" = 1 ]; then
    printf '%-26s %s\n' "$t" "$verdict"
  else
    printf '%-26s %s   [exit %s]\n' "$t" "$verdict" "$code"
    fail=1
    failed_suites+=("$t")
    tail -n 12 "$log" | sed 's/^/    | /'
    [ "$code" = 124 ] && echo "    | (timed out after 600s)"
  fi
done

echo "----------------------------------------"
if [ "$fail" = 0 ]; then
  echo "GATE: PASS"
  rm -rf "$logdir"
else
  echo "GATE: FAIL — ${#failed_suites[@]} suite(s): ${failed_suites[*]}"
  echo "  full logs: $logdir"
fi

exit "$fail"
