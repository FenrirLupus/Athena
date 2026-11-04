#!/bin/bash
# Release gate: every suite, plus the permissions manager's own.
cd "$(dirname "$0")/.." || exit 1
PY=../.venv/bin/python3
fail=0
for t in version_check identity_check permissions_check approval_check self_modify_check scope_check install_request_check \
         plugin_install_check tool_install_check box_request_check security_check \
         tools_skills_plugins toolpacks_check sandbox_and_budget_check subagents_check instruction_docs_check \
         plugin_isolation_check plugins_safety_check admission_check idle_sleep waking_state \
         routing_check lifecycle_check config_parity lane_plugins_check updater_check comment_style_check publish_safety_check; do
  line=$(timeout 600 "$PY" "tests/$t.py" 2>&1 | grep -aE '^(RESULT|SYSTEM)|— RESULT|^ *[0-9]+/[0-9]+' | tail -1)
  printf '%-26s %s\n' "$t" "${line:-NO RESULT}"
  case "$line" in
    *FAIL*|"" ) fail=1 ;;
  esac
done
echo "----------------------------------------"
[ "$fail" = 0 ] && echo "GATE: PASS" || echo "GATE: FAIL"
