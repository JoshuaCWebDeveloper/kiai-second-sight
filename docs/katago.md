# KataGo setup and configuration

Kiai uses KataGo for game analysis. Run:

```bash
kiai setup
```

before importing games.

## Managed setup

By default, Kiai manages KataGo under `~/.kiai-second-sight/katago`. Setup downloads the required runtime, analysis config, and neural-network model, validates that they work together, and caches them for later runs.

With `managed_backend = "auto"`, setup prefers the current OpenCL build from source and falls back to compatible prebuilt runtimes only if the source build cannot run. OpenCL may perform a one-time device/model tuning pass on first use; KataGo caches those tuning results. Kiai also benchmarks a few batch-analysis thread layouts and writes the selected layout into its managed analysis config.

On Linux, the `source-opencl` strategy builds KataGo v1.18.1 locally against the host OpenCL runtime and uses the `b10c384h6nbttflrs` transformer model—the same model configured by KaTrain 1.20.0. Kiai supplies its own recent CMake and OpenCL headers when needed; the host still needs a C++ compiler, zlib development headers, and a working OpenCL runtime. The resulting binary and build inputs are cached under the managed KataGo directory.

You can force a backend/strategy with `managed_backend` in `kiai.toml`. Supported values are `auto`, `source-opencl`, `opencl`, `eigenavx2`, and `eigen`.

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

Kiai retains the Ubuntu-20.04-compatible KataGo v1.15.3 prebuilt runtime with the `b18c384nbt-uec` model as a fallback. The source-build strategy avoids that compatibility pin by compiling KataGo v1.18.1 on the local Linux system and can therefore use the newer transformer model.

## Import behavior

`kiai import` never installs or downloads KataGo resources. If the configured runtime is missing or incomplete, it asks you to run `kiai setup` first.
