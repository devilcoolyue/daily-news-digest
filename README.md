# Daily News Digest

Generate a daily vertical summary infographic (default: AI domain) with a pipeline:

1. Fetch items from configured sources (RSS/API/mock)
2. Deduplicate similar headlines into events
3. Score and rank events
4. Select top cards with diversity constraints
5. Render a final image + JSON manifest

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Offline demo (uses mock source only)
PYTHONPATH=src python3 -m daily_infographic.cli run --domain ai --mock-only
```

Outputs are written to `output/`.

## Real-source run

```bash
PYTHONPATH=src python3 -m daily_infographic.cli run --domain ai
```

If network sources are unreachable, the pipeline will skip failed sources and continue.

## LLM title refinement (optional)

Set `DEEPSEEK_API_KEY` (or `LLM_API_KEY`) in `.env` to enable LLM enrichment in domains where:

- `llm.enable_title_refine: true`
- `llm.enable_summary_refine: true`
- `llm.enable_icon_classify: true`
- `llm.batch_size: 12` (set to `top_k` for single-batch request/response)

The pipeline will still run with heuristic fallback when LLM calls fail.

## Project layout

- `configs/domains/ai.yaml`: domain strategy and source config
- `src/daily_infographic/`: pipeline implementation
- `output/`: generated images and manifests

`render.layout_mode` supports `smart` (default): highest-score card is rendered as the only hero card, and the rest auto-size by text density for more readable layouts.

## Multi-domain extension

To add a new domain (e.g., defense or entertainment), copy `configs/domains/ai.yaml` to a new domain file and tune:

- source list
- scoring weights
- entity and tag keywords
- visual theme
