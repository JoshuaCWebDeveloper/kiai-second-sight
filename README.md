# Kiai: Second Sight

Turn mistakes from your Go games into visual study cards.

Second Sight analyzes an SGF with KataGo, finds moves **you** played where your win rate was above 50% before the move and the move lost at least 10 percentage points, then creates a two-slide card:

1. **Question** — the board immediately before your move.
2. **Answer** — the exact same position with a KaTrain-style KataGo analysis overlay.

The generated deck is a square PowerPoint slideshow at `deck/second-sight.pptx`.

## Status

This is the initial working implementation. It already includes:

- SGF parsing and player-color inference
- KataGo JSON analysis-engine integration
- configurable win-rate filtering
- question/answer board rendering
- ownership heatmap and top-move overlays
- marking the move you actually played
- idempotent manifest updates keyed by SGF content + move number
- `.pptx` deck generation

The rendering is intentionally our own rather than GUI automation of KaTrain. KataGo is the analysis source; Second Sight controls the study-card presentation.

## Install

Python 3.11+ is required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -e '.[dev]'
cp kiai.example.toml kiai.toml
```

Edit `kiai.toml` to point at a KataGo executable, model, and **analysis config that reports win rates as BLACK**. KaTrain's bundled KataGo analysis config uses `reportAnalysisWinratesAs = BLACK`, which is the expected configuration for this version.

You may instead set:

```text
KIAI_KATAGO_PATH
KIAI_KATAGO_MODEL
KIAI_KATAGO_CONFIG
```

## Use

If your name is configured under `[player].names`:

```bash
kiai import path/to/game.sgf
```

Or specify the color explicitly:

```bash
kiai import path/to/game.sgf --me white
```

The command analyzes every position immediately before and after one of your moves. For move `M`, it compares KataGo's evaluation at turns `M-1` and `M`.

By default a card qualifies when:

```text
my win rate before move > 50%
and
my win rate before - my win rate after >= 10 percentage points
```

For example, 68% → 54% creates a card; 48% → 30% does not.

Outputs:

```text
cards/
  images/
    <game>-m047-question.png
    <game>-m047-answer.png
  manifest.json
deck/
  second-sight.pptx
```

Rebuild only the PowerPoint from the current manifest:

```bash
kiai rebuild-deck
```

## Why the answer uses the pre-move position

The answer is deliberately **not** the position after the mistake. Both sides represent the same position. The question asks what you should play; the answer reveals KataGo's recommendations for that position and marks the move you actually chose.

## KataGo protocol

Second Sight launches KataGo in its JSON analysis mode:

```bash
katago analysis -model MODEL -config CONFIG
```

A whole game is submitted with `analyzeTurns`, so KataGo can analyze the requested positions efficiently. The query requests ownership data as well as root and candidate-move statistics.

## Development

```bash
pytest
ruff check .
```

The test suite does not require a KataGo binary; protocol integration is isolated from SGF parsing, filtering, and rendering.

## Roadmap

Near-term improvements:

- detect KaTrain's KataGo/model/config paths automatically
- optional fast first pass + deep re-analysis around the threshold
- closer visual matching of KaTrain's ownership and candidate overlays
- inbox/watcher mode for one-step SGF ingestion
- Anki export alongside PowerPoint
- richer manifest/cache separation so ownership arrays do not bloat the manifest
