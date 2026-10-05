# Operator GUI diagnostics

These are operator-driven backend diagnostics on the TempleOS 5.03 ISO using
QEMU 10.0.13 and the prepared snapshot. They are not provider benchmark runs,
model benchmark scores, or human task grades.
Every action has before/after screenshots in its `trajectory.jsonl` file.
Both validation VMs were closed afterward.

## Built-in documentation

Artifacts: [documentation trajectory](../examples/gui-validation/documentation/trajectory.jsonl)
and [final screenshot](../examples/gui-validation/documentation/final.png).

The check opened the real help index with `Ed("::/Doc/HelpIndex.DD");`, clicked
the visible Command Line link at `(65, 75)`, then pressed Enter. The final
screenshot shows the real **Command Line Overview** page and its explanation:
“The cmd line feeds into the HolyC compiler line-by-line as you type.”

The sequence is visible in [0002.png](../examples/gui-validation/documentation/0002.png)
(index), [0003.png](../examples/gui-validation/documentation/0003.png) (clicked
link), [0004.png](../examples/gui-validation/documentation/0004.png) (opened
document), and the final screenshot (document left at its beginning).
The target document was reached through the index, without a direct shell
command to open the target file.

## File manager

Artifacts: [file-manager trajectory](../examples/gui-validation/file-manager/trajectory.jsonl)
and [final screenshot](../examples/gui-validation/file-manager/final.png).

The check launched `FileMgr;`. Mouse input changed the directory tree and
opened the file-manager popup; a later click opened the real CompileDemo
source, visible in [0008.png](../examples/gui-validation/file-manager/0008.png).
Keyboard navigation and Space expanded `/Demo/Graphics`, visible in
[0026.png](../examples/gui-validation/file-manager/0026.png). The final screen has Blot selected
inside that tree.

Opening Blot itself remained unverified. File-manager clicks have selection
and drag/drop semantics, and one attempted interaction produced a read-only
boot-drive write error. This check does not establish that the full file
manager sample task passes. Its trajectory is retained for review.

Separate operator diagnostics confirmed `CURSOR=100,100` after an absolute
mouse move and `BENCH_ARITH=391` after typing through QMP key events.
