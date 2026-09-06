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

Python 3.11+ and Poetry are required. KataGo does **not** need to be installed separately by default.

If Poetry is not installed yet, one common installation method is:

```bash
pipx install poetry
```

### 1. Install Python 3.11 with pyenv and install the project

Poetry manages the project's virtual environment and dependencies, while pyenv manages the Python interpreter itself. Install pyenv first if it is not already available:

```bash
curl -fsSL https://pyenv.run | bash
```

The installer downloads pyenv but does not itself modify your shell startup files. The current official pyenv instructions provide a separate command that installs the recommended shell setup automatically:

```bash
~/.pyenv/bin/pyenv init --install
```

Then reload Bash so the PATH and shim changes take effect:

```bash
source ~/.bashrc
```

This repository already commits the desired interpreter in `.python-version`. Install exactly that version rather than duplicating the version number in the setup instructions:

```bash
pyenv install --skip-existing "$(pyenv local)"
python --version
```

Because `pyenv local` reads the committed `.python-version`, the version declaration stays in one place. `--skip-existing` makes this setup command safe to rerun: pyenv installs the requested interpreter only when it is missing. Once installed, pyenv automatically resolves Python commands in this repository through that local version; no `pyenv local <version>` write command is needed after cloning. Tell Poetry to build its virtual environment from the interpreter selected by pyenv:

```bash
poetry env use "$(pyenv which python)"
```

Then install the locked dependencies and the `kiai` command into that environment:

```bash
poetry install
cp kiai.example.toml kiai.toml
```

To activate the Poetry environment in the current shell:

```bash
eval "$(poetry env activate)"
```

Once activated, normal shell commands resolve inside the project environment, so use `python`, `pytest`, `ruff`, and `kiai` directly rather than prefixing every command with `poetry run`.

Leave the environment with:

```bash
deactivate
```

### 2. Optional: install autoenv for automatic activation

The repository includes `.autoenv` and `.autoenv.leave`. With [autoenv](https://github.com/hyperupcall/autoenv) configured as below, entering the repository activates the Poetry environment and leaving the repository deactivates it. Pyenv already selects the interpreter declared by `.python-version` whenever Python commands are resolved from this repository, so no additional `pyenv shell` override is needed.

Install autoenv using the same setup documented in the JoshuaCWebDeveloper docs:

```bash
nvm use node
```

```bash
curl -#fLo- 'https://raw.githubusercontent.com/hyperupcall/autoenv/master/scripts/install.sh' | sh
```

The installer appends a line to `~/.bashrc` that sources `autoenv/activate.sh`. Add these variables to `~/.bashrc` immediately **before** that source line:

```bash
AUTOENV_ENABLE_LEAVE=yes
AUTOENV_ENV_FILENAME=.autoenv
AUTOENV_ENV_LEAVE_FILENAME=.autoenv.leave
```

Reload your shell after editing `~/.bashrc`:

```bash
source ~/.bashrc
```

On the first visit to the repository, autoenv may ask you to authorize the checked-in environment files. The Poetry environment must already exist, so run `poetry install` once before relying on automatic activation.

### 3. Configure and provision KataGo

Edit `kiai.toml` for your player name and any desired analysis settings, then run:

```bash
kiai setup
```

If no external KataGo paths are configured, `kiai setup` (or the first `kiai import`) downloads a pinned KataGo runtime, analysis config, and neural-network model into `~/.kiai-second-sight/katago`. Downloads are cached and reused. The default managed backend is portable CPU `eigen`; `eigenavx2` and `opencl` can be selected in `kiai.toml`.

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
