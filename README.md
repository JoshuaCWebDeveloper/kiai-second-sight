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

### Python via Pyenv

#### pyenv

Run:

```bash
apt install make build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev curl git libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev libzstd-dev
```

Run:

```bash
export PYENV_GIT_TAG=v2.8.5 && curl -fsSL https://pyenv.run | bash
```

Run:

```bash
~/.pyenv/bin/pyenv init --install
```

Reload your shell or rerun your shell script.

#### Python

**Dependencies:**

- pyenv

From inside the project root directory, run:

```bash
pyenv install --skip-existing "$(pyenv local)"
```

### Poetry w/ Pyenv

**Dependencies:**

- pyenv
- Python

From inside the project root directory, run:

```bash
curl -sSL https://install.python-poetry.org | python3 - --version 2.4.3
```

and then:

```bash
poetry env use python
```

### Autoenv

Installing autoenv eliminates the need to run `eval "$(poetry env activate)"` every time you `cd`
into the project.

**Dependencies:**

- Poetry

Run:

```bash
curl -#fLo- 'https://raw.githubusercontent.com/hyperupcall/autoenv/master/scripts/install.sh' | sh
```

The above command will append a line to your `~/.bashrc` file that sources
`autoenv/activate.sh`. Add the following variables to your `~/.bashrc` file
immediately _before_ the source line:

```bash
AUTOENV_ENABLE_LEAVE=yes
AUTOENV_ENV_FILENAME=.autoenv
AUTOENV_ENV_LEAVE_FILENAME=.autoenv.leave
```

### Project

Once all above dependencies are installed, run:

```bash
eval "$(poetry env activate)"

poetry install
```

#### Configure and provision KataGo

Create your local configuration from the checked-in example:

```bash
cp kiai.example.toml kiai.toml
```

Edit `kiai.toml` to set your player name and any desired analysis settings, then provision KataGo:

```bash
kiai setup
```

If no external KataGo paths are configured, `kiai setup` downloads a pinned KataGo runtime, analysis config, and neural-network model into `~/.kiai-second-sight/katago`. It launches the analysis engine with the downloaded model and config so setup verifies the complete managed runtime, not just that the KataGo executable starts. Downloads are cached and reused. The default managed backend is `auto`: during `kiai setup`, Kiai tries OpenCL, Eigen AVX2, and Eigen, benchmarks every backend that can actually run, then keeps the fastest one. It also runs a deliberately tiny batch-analysis benchmark across several thread layouts and writes an optimized managed analysis config; the benchmark is designed to keep setup interactive rather than perform a full-strength analysis. Set `managed_backend` explicitly in `kiai.toml` to force a backend instead.
On Linux, Kiai currently pins KataGo v1.15.3 because its prebuilt Linux binaries were built on Ubuntu 20.04. The managed model is the v1.12.4 `b18c384nbt-uec` network, which is compatible with KataGo v1.15.3; newer transformer models require KataGo v1.17+. Kiai also downloads and bundles Ubuntu 20.04's `libzip5` runtime alongside KataGo, so setup does not require installing `libzip5` system-wide. Newer KataGo Linux releases are built on Ubuntu 22.04 and require newer libraries such as OpenSSL 3. Windows uses the newer managed KataGo release.

`kiai setup` is required before importing games. `kiai import` never downloads or installs KataGo resources; if the configured managed runtime is missing, it tells you to run setup first.

To reuse an existing KataGo or KaTrain installation, set **all three** of `katago_path`, `model_path`, and `config_path` under `[analysis]`. A complete external configuration takes precedence and suppresses managed downloads. The equivalent environment variables are `KIAI_KATAGO_PATH`, `KIAI_KATAGO_MODEL`, and `KIAI_KATAGO_CONFIG`. The analysis config must report win rates as BLACK.

## Use

If your name is configured under `[player].names`:

```bash
kiai import path/to/game.sgf
```

Or specify the color explicitly:

```bash
kiai import path/to/game.sgf --me white
```

Imports use a two-pass analysis. By default Kiai first screens all of your moves at 25 visits, using KataGo's searched child evaluation for the move you actually played when available and falling back to a cheap post-move search when necessary. Only plausible mistakes are then re-analyzed at the configured full `max_visits` depth (500 visits by default). The final card filter always uses the full-depth before/after evaluations.
The defaults are 25 visits for screening and 500 visits for full analysis, matching KaTrain's 500-visit default analysis depth while keeping the screening pass lightweight.

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
