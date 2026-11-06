# solidcraft — install

Pin: **SolidCraft v0.30.1** (git tag `v0.30.1`, commit `8e2b0cd60d14b4152b12b8f1fdb2bcfdcd8c52f4`,
<https://github.com/tangency-labs/solidcraft>). Upstream is used unmodified. The benchmark only
needs the `solidcraft-cli` binary. It runs the agent's MCP server (`solidcraft-cli mcp --headless`)
and every adapter call (`run`, `eval`, `script`). Re-run the conformance suite after every version
bump (05_TOOLS.md).

## A. Build from source (reference route; how the pin was verified)

You need Rust **≥ 1.95** (`rust-version` in the workspace `Cargo.toml`) and a C toolchain. The CLI
needs no GPU and no window system. On Linux the binary links only libc, libm and libgcc_s.

```bash
git clone --branch v0.30.1 --depth 1 https://github.com/tangency-labs/solidcraft
cd solidcraft
git rev-parse HEAD                      # 8e2b0cd60d14b4152b12b8f1fdb2bcfdcd8c52f4
cargo build --release -p solidcraft-cli # ~12 min on the Linux x86_64 reference host
./target/release/solidcraft-cli --version   # -> solidcraft-cli 0.30.1
```

The release profile keeps debug info, so the binary is about 470 MB. To get a smaller binary without
changing the source, build with `CARGO_PROFILE_RELEASE_DEBUG=false cargo build --release -p
solidcraft-cli`. Put the binary on `PATH`, or point the adapter at it:

```bash
export RECAD_SOLIDCRAFT_CLI=/path/to/solidcraft/target/release/solidcraft-cli
```

Verified 2026-11-06: rustc 1.97.0, Linux x86_64, conformance 10/10.

## B. Release installers (desktop hosts)

The release packages ship `solidcraft-cli` next to the app. Install v0.30.1 from
<https://github.com/tangency-labs/solidcraft/releases/tag/v0.30.1>:

| Host | Package |
|---|---|
| Windows x64 / ARM64 | `solidcraft-windows-x64.msi` / `solidcraft-windows-arm64.msi`. The CLI is at `C:\Program Files\SolidCraft\solidcraft-cli.exe` (older installers don't put it on `PATH`) |
| macOS | `solidcraft-macos-universal.dmg` (signed and notarized) |
| Linux x86_64 / aarch64 | `.AppImage`, `.deb`, `.rpm`, Arch `.pkg.tar.zst`, or `snap install solidcraft` |

Use the files of the pinned tag, not `releases/latest`. Check with `solidcraft-cli --version`, which
must print `solidcraft-cli 0.30.1`. The release route has not been run through conformance yet.
Before a host enters Track T, run `pytest backends/solidcraft/tests` on it.

## C. MCP registration (agent side)

The `recadbench compose` step writes the run's MCP config from `backend.yaml`. Do not register by
hand for benchmark runs. The equivalent entry is:

```json
{"mcpServers": {"solidcraft": {"command": "solidcraft-cli",
  "args": ["mcp", "--headless", "--allow-path", "<run_dir>/cad"]}}}
```

- Pass `--headless` explicitly. Plain `solidcraft-cli mcp` attaches to a running SolidCraft desktop
  app if one is open. That breaks run isolation and `max_parallel: 2`.
- File tools (`save`, `export`, `open`) only work inside allowed folders. `--allow-path` gives the
  run's `cad/` dir. Never use `--allow-any-path`.
- Server name `solidcraft` gives the tool prefix `mcp__solidcraft__`, which `cad_prefixes` in
  `backend.yaml` relies on.
- For interactive use outside the benchmark: `claude mcp add solidcraft -- solidcraft-cli mcp`, or
  `solidcraft-cli mcp setup --client claude-code`.

Native artifact of a run: the agent saves the design (`save` → `cad/part.sc`) or exports its
history (`execute timeline.export_script {"path": "cad/part.json"}`). The adapter accepts both
formats. It converts `.sc` with `solidcraft-cli script`.

## D. Smoke test

```bash
export RECAD_SOLIDCRAFT_CLI=$(command -v solidcraft-cli)
python3 backends/solidcraft/adapter/adapter.py version
python3 backends/solidcraft/adapter/adapter.py replay C01_block --out-dir /tmp/sc_C01
#   -> {"ok": true, "volume_mm3": 24000.0, "faces": 6, "count": 1, ...}
pytest backends/solidcraft/tests          # 32 tests; uses build123d for an exact STEP volume if present
```

## E. Termux (Android ARM64): test steps (unverified)

`termux` is **not** in `backend.yaml` `hosts` until these steps pass on the phone. Nobody has run
them yet. Run them in Termux (F-Droid build, aarch64) and record the outcome in `STATUS.md`.

1. Toolchain:
   ```bash
   pkg update && pkg install -y git rust clang python binutils
   rustc --version        # must be >= 1.95; if Termux's rust is older, stop: record "blocked: rustc X.Y"
   uname -m               # aarch64
   ```
2. Build (needs about 4 GB of free storage and is slow; limit jobs to avoid the OOM killer):
   ```bash
   git clone --branch v0.30.1 --depth 1 https://github.com/tangency-labs/solidcraft ~/solidcraft
   cd ~/solidcraft && git rev-parse HEAD     # 8e2b0cd60d14b4152b12b8f1fdb2bcfdcd8c52f4
   CARGO_BUILD_JOBS=2 CARGO_PROFILE_RELEASE_DEBUG=false \
     cargo build --release -p solidcraft-cli 2>&1 | tee ~/sc_build.log
   ./target/release/solidcraft-cli --version # solidcraft-cli 0.30.1
   ```
   Record the wall time, and the first error if the build fails. A likely failure point is a
   dependency without Android/Bionic support.
3. Fallback if step 2 fails: try the glibc `linux-aarch64` release binary. Extract `solidcraft-cli`
   from `solidcraft-linux-aarch64.deb` (`ar x`, then `tar xf data.tar.*`). Run it with
   `pkg install glibc-repo && pkg install glibc-runner` and then `grun ./solidcraft-cli --version`,
   or inside `proot-distro login debian`. Record which route works. A proot route means
   `max_parallel: 1` on the phone.
4. CLI functional check:
   ```bash
   export RECAD_SOLIDCRAFT_CLI=~/solidcraft/target/release/solidcraft-cli
   git clone https://github.com/<org>/ReCADBench ~/ReCADBench && cd ~/ReCADBench
   $RECAD_SOLIDCRAFT_CLI run backends/solidcraft/replays/C01_block.json --out ~/c01.step
   $RECAD_SOLIDCRAFT_CLI eval ~/c01.step | grep volume_mm3          # 24000.0
   ```
5. MCP check (headless server answers `tools/list` with 21 tools):
   ```bash
   printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
    | timeout 20 $RECAD_SOLIDCRAFT_CLI mcp --headless 2>/dev/null | tail -1 | python -c \
    'import json,sys; print(len(json.load(sys.stdin)["result"]["tools"]))'          # 21
   ```
   Also check that `screenshot` falls back to the CPU rasterizer without crashing. Run
   `solidcraft-cli gpu`, then
   `solidcraft-cli snapshot backends/solidcraft/replays/C01_block.json --out ~/c01.png`.
6. Conformance (the adapter is stdlib-only; pytest is optional):
   ```bash
   for c in $(ls -d backends/conformance/ir_cases/C*/ | xargs -n1 basename); do
     python backends/solidcraft/adapter/adapter.py replay $c --out-dir ~/sc_conf/$c 2>/dev/null
   done
   pip install pytest && python -m pytest backends/solidcraft/tests  # build123d absent -> CLI volumes only
   ```
   Pass means all 10 replays print `"ok": true` with the volumes and counts of
   `backends/conformance/ir_cases/*/expected.json`, and all tests pass.
7. If it passes, add `termux` to `hosts` in `backend.yaml` and note the build route
   (native / glibc-runner / proot) in the comment at the top.
