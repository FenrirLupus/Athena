#!/usr/bin/env bash
# athena.sh — start Athena: activate the environment and hand off to the entry point.
#   ./athena.sh            the multiplexer
#   ./athena.sh serve      same, explicit
#   ./athena.sh restart    stop the running multiplexer, then start it again
#   ./athena.sh dev        serve with reload-on-change (one process for a whole edit cycle)
#   ./athena.sh box <cmd>  run any command inside the athena-agent distrobox (system deps live there)
#   ./athena.sh metrics    turn metrics: tokens, throughput, latency, cost, resources
#   ./athena.sh graph      rebuild the code graph into ~/.athena/graphify (outside the project)
#   ./athena.sh graph check  exit 1 when the graph is older than the code it describes
#   ./athena.sh stop       stop the running multiplexer
#   ./athena.sh status     is the multiplexer up, on which pid
#   ./athena.sh turn default "hello"
#   ./athena.sh selftest   offline loop check
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# canonicalise: a path containing ".." makes Python warn about sys.prefix on every run
PARENT="$(cd "$HERE/.." && pwd -P)"
VENV="$PARENT/.venv"
PORT="${ATHENA_PORT:-8765}"

if [ ! -x "$VENV/bin/python3" ]; then
  echo "missing venv at $VENV — create it with: python3 -m venv \"$VENV\" && \"$VENV/bin/pip\" install -r \"$HERE/requirements.txt\"" >&2
  exit 1
fi

export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"

# Athena's own credentials, and nothing else. `.athena/.secret` is dotenv-shaped (NAME=value), so a service
# definition and this launcher read the same file. An external app's env file is never sourced: Athena is a
# separate architecture, and borrowing another tool's environment would make its credentials part of this one.
if [ -f "$PARENT/.secret" ]; then
  set -a
  . "$PARENT/.secret"
  set +a
fi

# -- process control -------------------------------------------------------
# Stopping is its own command because restarting by hand meant a `kill` whose SIGTERM then shows up as a
# failed process everywhere else — the log says 143 and it reads like a crash when it was a restart.
listener_pid() {
  # `|| true`: with `set -euo pipefail` an empty grep aborted the whole script, so `status` printed
  # nothing at all when the server was down — the one case it exists for
  local pid
  pid="$(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
  printf '%s' "$pid"
}

listener_pids() {
  # ALL of them. Under `--reload` uvicorn keeps a parent that owns the socket and a child that serves; the
  # parent does not exit when the child is killed, so stopping "the pid" left the port bound and looked like
  # a stop that silently failed.
  ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
}

model_pids() {
  # the local model runtime is started detached, so it outlives the server that launched it: stopping the
  # server left ~8 GB of RSS and ~2.6 GB of VRAM held by a process nothing was talking to
  pgrep -f "athena-agent/providers/offload_runtim[e]\.py" 2>/dev/null || true
}

wait_free() {
  for _ in $(seq 1 40); do
    [ -z "$(listener_pid)" ] && return 0
    sleep 0.25
  done
  return 1
}

do_stop() {
  local pids pid models
  pids="$(listener_pids)"
  models="$(model_pids)"
  if [ -z "$pids" ] && [ -z "$models" ]; then
    echo "athena: nothing listening on $PORT and no model runtime running"
    return 0
  fi
  for pid in $pids; do
    echo "athena: stopping pid $pid on port $PORT"
    kill "$pid" 2>/dev/null || true
  done
  # the model runtime goes last and gets a longer grace: it has to free VRAM and write its state out
  if [ -n "$models" ]; then
    echo "athena: stopping the local model runtime (pids: $(echo $models | tr '\n' ' '))"
    for pid in $models; do kill "$pid" 2>/dev/null || true; done
    for _ in $(seq 1 24); do
      [ -z "$(model_pids)" ] && break
      sleep 0.5
    done
    for pid in $(model_pids); do
      echo "athena: model runtime $pid ignored SIGTERM; sending SIGKILL" >&2
      kill -9 "$pid" 2>/dev/null || true
    done
    if [ -z "$(model_pids)" ]; then echo "athena: model runtime stopped"; else echo "athena: model runtime STILL running" >&2; fi
  fi
  if wait_free; then
    echo "athena: port $PORT is free"
    return 0
  fi
  for pid in $(listener_pids); do
    echo "athena: pid $pid ignored SIGTERM; sending SIGKILL" >&2
    kill -9 "$pid" 2>/dev/null || true
  done
  wait_free && echo "athena: port $PORT is free" || { echo "athena: port $PORT still bound" >&2; return 1; }
}

case "${1:-serve}" in
  stop)
    # the credential proxy is a child of the server, and an orphan of it still holds keys in memory
    pkill -f "core[.]credproxy" >/dev/null 2>&1 || true
    do_stop
    exit $?
    ;;
  restart)
    do_stop || exit 1
    echo "athena: starting"
    exec "$VENV/bin/python3" -m cli serve
    ;;
  box)
    # Run Athena inside the `athena-agent` distrobox. The Python venv stays the app's own environment (it is
    # mounted with $HOME, so nothing is duplicated); the box carries the *system* layer — compilers, CUDA
    # headers, libraries — which must never be installed onto an atomic host like Bazzite.
    #   ./athena.sh box serve        the server, inside the box
    #   ./athena.sh box status       any athena command, wrapped
    shift
    if ! distrobox list 2>/dev/null | awk -F'|' 'NR>1 {gsub(/ /,"",$2); print $2}' | grep -qx "athena-agent"; then
      echo "athena: no distrobox named 'athena-agent'." >&2
      echo "  create it with: distrobox create --name athena-agent --image docker.io/library/ubuntu:24.04 --nvidia --yes" >&2
      exit 1
    fi
    cmd="cd $(printf '%q' "$HERE") && ./athena.sh"
    for arg in "$@"; do cmd="$cmd $(printf '%q' "$arg")"; done
    exec distrobox enter athena-agent -- bash --norc -c "$cmd"
    ;;
  dev)
    # development: one long-lived process that reloads itself when code changes, so a fix does not need a
    # restart (and every restart stops the previous server, whose session then reports a SIGTERM)
    existing="$(listener_pid)"
    if [ -n "$existing" ]; then
      echo "athena: already running on $PORT (pid $existing) — stop it first" >&2
      exit 1
    fi
    exec "$VENV/bin/python3" -m cli serve --reload
    ;;
  graph)
    # the graph is an *evaluation* artefact, not part of the project: it is written beside the tree, never
    # inside it, so the project stays exactly what it is and the graph can be rebuilt or thrown away freely
    if ! command -v graphify >/dev/null 2>&1; then
      echo "athena: graphify is not installed (pipx install graphifyy)" >&2
      exit 1
    fi
    out="${ATHENA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}/graphify"

    # `graph check`: is the graph older than the code it describes? Exit 1 when stale, so "the graph is
    # current" is a thing that can be verified instead of assumed.
    if [ "${2:-}" = "check" ]; then
      if [ ! -f "$out/graph.json" ]; then echo "athena: no graph yet — run './athena.sh graph'" >&2; exit 1; fi
      # the stamp, not graph.json's mtime: graphify leaves its outputs untouched when the topology did not
      # change, so the file's mtime says "when it last differed", not "when it was last verified".
      # `.md` is included because graphify indexes documentation too — without it the check called the graph
      # "current" while its doc nodes were stale, which is a claim about code pretending to be about the corpus
      stamp="$out/.last_build"
      [ -f "$stamp" ] || stamp="$out/graph.json"
      newer="$(find . -path ./graphify -prune -o \( -name '*.py' -o -name '*.js' -o -name '*.html' -o -name '*.css' -o -name '*.sh' -o -name '*.yaml' \
                   -o -name '*.md' \) -type f -newer "$stamp" -print 2>/dev/null | head -5)"
      if [ -n "$newer" ]; then
        echo "athena: graph is STALE — changed since it was built:"
        printf '  %s\n' $newer
        echo "  rebuild with: ./athena.sh graph"
        exit 1
      fi
      echo "athena: graph is current ($out/graph.json)"
      exit 0
    fi
    mkdir -p "$out"
    echo "athena: rebuilding the code graph into $out"
    GRAPHIFY_OUT="$out" graphify update "$(cd "$(dirname "$0")" && pwd)" 2>&1 | grep -Ev "^\s*$" | tail -8
    touch "$out/.last_build"        # "the graph was verified against this code at this time"
    echo "athena: open $out/graph.html · report $out/GRAPH_REPORT.md"
    exit 0            # the case only pre-dispatches; without this the CLI's own dispatcher runs next
    ;;
  metrics)
    # `$@` already carries "metrics" as its first word; passing it again made `metrics rollup` arrive as
    # ["metrics", "metrics", "rollup"] and the subcommand was never seen
    shift
    exec "$VENV/bin/python3" -m cli metrics "$@"
    ;;
  update)
    # the updater stops, swaps and restarts the tree it lives in, so it runs on the venv interpreter with the port
    # it must health-check; it must never inherit a half-read `$1` from this dispatcher
    shift
    ATHENA_PORT="$PORT" exec "$VENV/bin/python3" -m updater "$@"
    ;;
  status)
    pid="$(listener_pid)"
    if [ -n "$pid" ]; then
      echo "athena: up on $PORT (pid $pid)"
      curl -s --max-time 3 "http://127.0.0.1:$PORT/health" || echo "athena: no health response"
      echo
    else
      echo "athena: down (nothing on $PORT)"
      exit 1
    fi
    exit 0
    ;;
esac

# A second `serve` while one is already listening fails with EADDRINUSE, which reads as a crash of the new
# process under `restart`. The check below states it plainly instead.
if [ "${1:-serve}" = "serve" ] || [ "$#" -eq 0 ]; then
  existing="$(listener_pid)"
  if [ -n "$existing" ]; then
    echo "athena: already running on $PORT (pid $existing) — use './athena.sh restart' or 'stop'" >&2
    exit 1
  fi
fi

exec "$VENV/bin/python3" -m cli "$@"
