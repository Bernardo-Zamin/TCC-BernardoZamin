# Everything runs inside the container. The host only needs Docker.
# First time: cp .env.example .env, fill in the keys, make build.

MODEL := qwen3-32b
SUITE := workspace
ATTACK := important_instructions
TASKS := 3
INJECTIONS := 2

COMPOSE := docker compose -f docker/docker-compose.yml
RUN := $(COMPOSE) run --rm harness

.PHONY: build test openclaw campaign progress campaign-stop verify tables shell clean

build:
	$(COMPOSE) build

# Unit tests. No network and no cost.
test:
	$(RUN) python3 -m unittest discover -s tests -v

# One attack against OpenClaw, outside the campaign (writes to results/_smoke).
# Costs money.
openclaw:
	$(COMPOSE) run --rm -e TCC_OUT_DIR=results/_smoke harness \
	  sh -c 'export HOME=/tmp/oc && mkdir -p /tmp/oc && \
	    python3 scripts/run_openclaw.py --suite $(SUITE) --attack $(ATTACK) \
	      --model $(MODEL) --user-tasks $(TASKS) --injection-tasks $(INJECTIONS)'

# Full campaign in the background. Recorded episodes are skipped. Costs money.
#   make campaign
#   make campaign MODELS=gpt-oss-120b WORKERS=2 REPS=2-10
MODELS :=
REPS :=
WORKERS := 2
campaign:
	@mkdir -p results/logs
	@chmod +x scripts/campaign.sh
	@for k in $$(seq 0 $$(($(WORKERS) - 1))); do \
	  MODELS="$(MODELS)" REPS="$(REPS)" \
	    setsid nohup scripts/campaign.sh $$k $(WORKERS) >/dev/null 2>&1 & \
	  echo "started worker $$k of $(WORKERS) ($(or $(MODELS),all models))"; \
	done
	@sleep 2 && $(MAKE) --no-print-directory progress

# Episodes done, pace and spend. Runs on the host.
progress:
	@python3 scripts/show_progress.py

campaign-stop:
	@for f in results/logs/worker*.pid; do \
	  [ -f "$$f" ] && kill -- -$$(cat $$f) 2>/dev/null && echo "stopped: $$f"; \
	done; true
	@docker ps -q --filter ancestor=docker-harness | xargs -r docker stop

# Check the recorded episodes: complete grid, no duplicates.
verify:
	$(RUN) python3 scripts/verify_results.py

# results/tables.md, results/tables.csv and results/episodes.csv.
tables:
	$(RUN) python3 scripts/make_tables.py

shell:
	$(COMPOSE) run --rm --entrypoint bash harness

# Delete the episodes. They are paid and cannot be recovered: needs CONFIRM=yes.
clean:
	@if [ "$(CONFIRM)" != "yes" ]; then \
	  echo "Aborted. To delete results/agentdojo-openclaw:"; \
	  echo "  make clean CONFIRM=yes"; \
	  exit 1; \
	fi
	rm -rf results/agentdojo-openclaw
