#!/usr/bin/env bash
# One campaign worker. Runs on the host.
#
#   scripts/campaign.sh <k> <n>     worker k of n
#
# Optional environment variables (set by `make campaign`):
#   MODELS="gpt-oss-120b"       only these models (default: all)
#   REPS="1" or "2-10"          only these repetitions (default: 1..10)
#
# A rerun resumes, because each chunk skips recorded episodes.
set -u

K="${1:?worker index}"
N="${2:?worker count}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Log and pid carry the model names, so two campaigns can run at the same time.
MODELS="${MODELS:-}"
REPS="${REPS:-}"
if [ -n "$MODELS" ]; then
  TAG="$(echo $MODELS | tr ' ' '+')"
  NAME="worker-$TAG-$K"
else
  NAME="worker$K"
fi
LOGS="results/logs"; mkdir -p "$LOGS"
exec >> "$LOGS/$NAME.out" 2>&1
echo $$ > "$LOGS/$NAME.pid"

# .git is not mounted in the container, so the commit goes by environment variable
# and is recorded in each episode.
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"

# The (model, repetition) chunks of this worker.
CHUNKS="$(python3 - "$K" "$N" <<'PY'
import os
import sys
sys.path.insert(0, ".")
from harness.campaign import CAMPAIGN_MODELS, chunks, split_chunks
k, n = int(sys.argv[1]), int(sys.argv[2])
models = os.environ.get("MODELS", "").split() or None
for m in models or []:
    if m not in CAMPAIGN_MODELS:
        sys.exit(f"model not in harness/campaign.py: {m}")
reps = []
for tok in os.environ.get("REPS", "").split():
    a, _, b = tok.partition("-")
    reps += range(int(a), int(b or a) + 1)
for m, r in split_chunks(chunks(models, reps or None), n)[k]:
    print(f"{m} {r}")
PY
)" || { echo "!!! invalid chunk list, worker $K did not run"; rm -f "${LOGS:?}/${NAME:?}.pid"; exit 1; }

total=$(echo "$CHUNKS" | grep -c .)
echo "=== worker $K/$N, $total chunks, commit $COMMIT, $(date '+%F %T') ==="

i=0
# Here-string, not a pipe: with a pipe, the loop runs in a subshell and `exit` would
# not stop the script.
while read -r MODEL REP; do
  [ -z "$MODEL" ] && continue
  i=$((i+1))
  echo "--- [$i/$total] $MODEL rep$REP, $(date '+%F %T') ---"

  attempt=1
  while [ "$attempt" -le 3 ]; do
    # `< /dev/null` is required: otherwise `docker compose run` reads the loop's
    # stdin and consumes the remaining chunks. `-T` is required because there is
    # no terminal under nohup.
    docker compose -f docker/docker-compose.yml run --rm -T \
      -e "TCC_COMMIT=$COMMIT" harness \
      sh -c "export HOME=/tmp/oc && mkdir -p /tmp/oc && \
             python3 scripts/run_chunk.py --model $MODEL --rep $REP" \
      < /dev/null
    code=$?

    case $code in
      0|2) break ;;                        # done, or nothing to do
      3)   echo "!!! budget exhausted, worker $K stopping"; exit 3 ;;
      4)   echo "!!! circuit breaker, worker $K stopping"; exit 4 ;;
      *)   echo "!!! exit code $code on attempt $attempt/3, waiting 60 s"
           sleep 60; attempt=$((attempt+1)) ;;
    esac
  done
done <<< "$CHUNKS"

echo "=== worker $K/$N finished, $i of $total chunks, $(date '+%F %T') ==="
