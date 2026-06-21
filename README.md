# Pagination Agent

Production-oriented prototype for discovering job listing patterns, detecting pagination strategies, executing pagination or infinite scroll, and extracting jobs from dynamic career sites.

The project is organized around standalone pipeline services so each capability can later become its own worker or queue consumer without rewriting the core logic.

## What It Does

- Loads a job listing page in Playwright through Chrome DevTools Protocol.
- Builds a reusable job extraction pattern with the `job_pattern` layer.
- Detects pagination using reduced page HTML and LLM analysis.
- Executes URL, click, hash, load-more, and repaired pagination plans.
- Detects and executes infinite scroll only when scrolling proves new jobs are appearing.
- Performs bottom-continuation discovery only after scroll extraction has increased jobs.
- Tracks page state, decisions, and execution evidence for debugging.
- Persists job patterns, pagination patterns, page states, decisions, and run summaries.

## Repository Structure

```text
source/
  main.py                       # top-level entrypoint

  job_pattern/                  # job extraction pattern generation and validation
    job_main.py
    utils/
      extraction.py
      html_extraction.py
      logging.py
      openai_service.py
      patterns.py

  pagination/                   # pagination discovery and execution primitives
    browser.py                  # Playwright helpers and HTML reduction
    click.py                    # click target selection and control state checks
    discovery.py                # pagination and bottom-continuation discovery
    executor.py                 # URL/click pagination execution and repair loop
    llm.py                      # pagination LLM calls
    prompts.py                  # pagination prompt rules
    state.py                    # URL state helpers
    url_utils.py

  extraction/
    extractor.py                # job pattern bridge used by pipeline services

  infinite_scroll/
    detector.py                 # scroll probe and infinite-scroll detection
    executor.py                 # iterative infinite-scroll execution

  pipeline/
    runner.py                   # orchestration layer
    services.py                 # standalone pipeline services
    context.py                  # shared run context and stage results
    contracts.py                # service/action/pattern result contracts
    persistence.py              # artifact persistence
    policy.py                   # centralized pipeline decisions
    state.py                    # page state snapshots

  validation/
    jobs.py                     # final job validation summary
```

Generated outputs, logs, browser debug artifacts, and virtual environments are intentionally ignored by Git.

## Requirements

- Python 3.11+
- Chromium or Chrome with remote debugging enabled
- OpenAI API key

Install dependencies:

```bash
python3 -m venv my-env
source my-env/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Set your OpenAI key:

```bash
export OPENAI_API_KEY="your-key"
```

Start Chrome with CDP enabled:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/pagination-agent-chrome
```

Run the pipeline:

```bash
python -m source.main
```

The default CDP URL is:

```text
http://localhost:9222
```

You can update test URLs in:

```text
source/pagination/config.py
```

## Runtime Artifacts

Each run can persist evidence under:

```text
pagination_output/runs/
```

Typical artifacts:

```text
patterns/job_pattern.json
patterns/pagination_patterns.json
evidence/page_states.json
evidence/decisions.json
run_summary.json
```

These are ignored by Git because they are generated runtime data.

## Pipeline Flow

```text
PageLoadService
  -> JobExtractionService
  -> PaginationDiscoveryService
  -> PaginationExecutionService
  -> InfiniteScrollService, when pagination is absent or inconclusive
  -> BottomContinuationService, only when scroll added jobs
  -> ValidationService
```

The orchestrator owns flow decisions. Services own capabilities.

This keeps the code ready for future queue-based scaling where extraction, pagination discovery, pagination execution, and infinite scroll can run as separate workers.

## Production Notes

The pipeline records evidence before and after major actions:

- URL
- title
- page height
- job selector
- job count
- job signature hash
- pagination controls
- page indicators
- decisions and stop reasons

This makes failures easier to debug without rerunning the same site blindly.

## Important Git Hygiene

The following are ignored:

- `my-env/`
- `.playwright-mcp/`
- `source/logs/`
- `source/pagination_output/`
- `pagination_output/`
- `.env`
- Python cache/build files

Do not commit API keys, generated scrape outputs, browser debug files, or local virtual environments.
