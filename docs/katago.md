# KataGo setup and configuration

Kiai uses KataGo for game analysis. Run:

```bash
kiai setup
```

before importing games.

## Managed setup

By default, Kiai manages KataGo under `~/.kiai-second-sight/katago`. Setup downloads the required runtime, analysis config, and neural-network model, validates that they work together, and caches them for later runs.

With `managed_backend = "auto"`, setup tests the available accelerated backends and keeps the fastest working option. OpenCL may perform a one-time device/model tuning pass on first use; KataGo caches those tuning results. Kiai also benchmarks a few batch-analysis thread layouts and writes the selected layout into its managed analysis config.

You can force a backend with `managed_backend` in `kiai.toml`. Supported values are `auto`, `opencl`, `eigenavx2`, and `eigen`.

## External KataGo or KaTrain installation

To use an existing installation instead, set all three paths under `[analysis]`:

```toml
katago_path = "/path/to/katago"
model_path = "/path/to/model.bin.gz"
config_path = "/path/to/analysis.cfg"
```

A complete external configuration takes precedence over the managed runtime. The equivalent environment variables are:

```text
KIAI_KATAGO_PATH
KIAI_KATAGO_MODEL
KIAI_KATAGO_CONFIG
```

The analysis config must report win rates as Black so Kiai can normalize them to the player being reviewed.

## Current Linux compatibility

The managed Linux runtime currently uses KataGo v1.15.3 with the `b18c384nbt-uec` model because that prebuilt release is compatible with Ubuntu 20.04. Kiai bundles the required `libzip5` runtime alongside KataGo instead of requiring it system-wide.

Newer KataGo Linux binaries target newer Ubuntu environments, and newer transformer models require a newer KataGo engine. A future managed-runtime update can replace this compatibility stack with a newer engine built specifically for Ubuntu 20.04 rather than relying on the newer prebuilt binaries.

## Import behavior

`kiai import` never installs or downloads KataGo resources. If the configured runtime is missing or incomplete, it asks you to run `kiai setup` first.
