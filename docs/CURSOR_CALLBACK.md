# Cursor callback — TEST ONLY

This opt-in fixture JIT-compiles a HolyC callback in the shell. It runs at ring 0
in the shared guest address space during WinMgr refresh, drawing a cross as the
mouse moves. It is outside the default tasks and the measured six-task model run.

Run the scripted reference in a disposable VM:

```sh
.venv/bin/temple-cua run --provider scripted \
  --script scripts/reference/cursor_callback.json \
  --tasks tests/fixtures/tasks --task cursor_callback \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --output runs/cursor-callback-test
```

[Fixture](../tests/fixtures/tasks/cursor_callback.yaml) ·
[Reference replay](../scripts/reference/cursor_callback.json)

The operator replay completed in ten scripted turns, and the QEMU regression
passed. The cross moved before and after opening Command Line Overview. Its
manual grade stays `needs_review`; no model result is claimed.

Register with `Fs->draw_it=&Cross`; `&` supplies the callback address. The bare
assignment `Fs->draw_it=Cross;` produced a compiler error in the tested fixture.
WinMgr passes `(CTask *,CDC *)` with a window-relative drawing context. The exact
fixture retains its unnamed task parameter and raw `ms.pos` coordinates, so the
cross follows the pointer at `(+8,+16)` on the maximized bordered window.
Preserve that code when replaying the test.

The top-bar pulldown opens on hover during the right-click step. It can cover
the cross because menus render later; the callback remains installed. Ordinary
help/document navigation keeps the same task callback. The test closes its
isolated VM afterward.

Source: [WinZBuf.HC](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Demo/Graphics/WinZBuf.HC)
and [GrScrn.HC](https://github.com/cia-foundation/TempleOS/blob/c26482bb6ad3f80106d28504ec5db3c6a360732c/Adam/Gr/GrScrn.HC).

Run the opt-in QEMU regression with your prepared snapshot:

```sh
TEMPLE_CUA_CURSOR_CALLBACK_INTEGRATION=1 \
.venv/bin/python -m pytest tests/test_cursor_callback.py -m integration -q
```

Defaults are `assets/baseline.qcow2` and `qemu-system-x86_64`; override them with
`TEMPLE_CUA_BASELINE` and `TEMPLE_CUA_QEMU` if needed.
