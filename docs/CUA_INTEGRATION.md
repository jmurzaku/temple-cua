# Where Cua fits

The optional port is now implemented against released `cua-agent==0.9.0`.
It uses the actual ComputerAgent loop, an explicit custom computer handler,
and an OpenAI Responses compatibility bridge. See [CUA_AGENT.md](CUA_AGENT.md)
for commands, tested behavior and remaining limits. The section below records
the earlier architectural research; the dated statement about installation
describes that research session, before this implementation.

The historical assessment below informed the implemented
[Cua agent port](CUA_AGENT.md). That port now uses installed `cua-agent==0.9.0`
with the actual `ComputerAgent` loop and a TempleOS QMP adapter; its independent
local arithmetic fixture passed on 2026-10-05. See the linked guide for current
commands, compatibility changes and limitations.

Read-only research against Cua docs and GitHub main commit
`8674a6832086a38c40ea59886b977a82a97645ad` on 2026-10-04. No Cua package was
installed and no live TempleOS/Cua integration was tested.

Keep the known-good QEMU/QMP launcher, frozen resets and external UART judge.
Consider adding `cua-agent` above them for provider loops and trajectory exports.
Cua's own Bench adapter supplies ComputerAgent a dictionary of async screenshot,
dimensions, environment, click, double-click, type, keypress, move, scroll, drag
and wait functions. Map these to the existing backend, explicitly supply
640x480, and retain episode lifecycle/rewards outside that dictionary.

Current Cua supports local disk/ISO images and agentless QMP screenshots/input;
a guest daemon is not required for these basic actions. Its default QEMU setup
uses q35/virtio/USB devices, however, and does not establish compatibility with
TempleOS's legacy-device fixture. Rich guest shell/files/accessibility features
and its Linux/macOS/Windows daemon cannot be assumed available in TempleOS.

The documented `Sandbox.snapshot()` is unimplemented locally and in the cloud.
Keep this harness's actual frozen VM reset. Bench's current platform names are
Linux, Windows, macOS and Android, and normal setup/evaluation uses cua-spacesd;
TempleOS needs a custom adapter/session, not a turnkey task configuration.

Cua Bench exports ATIF trajectories and JSONL formats; its separate RL package
has replay buffers, workers and a Tinker GRPO trainer. These may help later with
parallel rollouts and trainable models. They do not enable training closed
OpenAI or Claude model weights through ordinary inference keys.

Licensing varies: agent/Bench/RL, SDK/CLI, Driver and Lume are MIT under the
repository licensing map. Spaces apps and cua-spacesd are FSL-1.1-MIT, with
commercial restrictions until release-specific conversion after two years.

Sources:

- https://cua.ai/docs/cua-sdk/reference/runtime-support
- https://cua.ai/docs/cua-sdk/reference/python/image
- https://cua.ai/docs/cua-sdk/reference/python/interfaces
- https://github.com/trycua/cua/blob/main/libs/python/agent/cua_agent/agent.py
- https://github.com/trycua/cua/blob/main/libs/cua-bench/cua_bench/agents/cua_agent.py
- https://github.com/trycua/cua/blob/main/libs/cua-bench/README.md
- https://github.com/trycua/cua/blob/main/libs/cua-bench-rl/README.md
- https://github.com/trycua/cua/blob/main/LICENSING.md
