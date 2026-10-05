# Cua compatibility patch

[openai-function-computer.patch](openai-function-computer.patch) targets Cua
commit [`033b980cd2d1ed744309843cfa8fe3c18ca368e6`](https://github.com/trycua/cua/commit/033b980cd2d1ed744309843cfa8fe3c18ca368e6).
The audited files match the installed `cua-agent==0.9.0` release.

The patch translates OpenAI computer function calls and history into Cua's action
protocol, validates active fields from the flat function schema, and resolves
custom-computer dimensions through the handler factory. It preserves native
computer-use-preview and unrelated function calls.

Focused tests: unmodified base **7 failed, 1 passed**; candidate **8 passed**.
They use mocked inference and the real `ComputerAgent`. No upstream PR has been
submitted. The full upstream suite and live desktop testing remain unrun.
The project's pinned adapter already implements its TempleOS compatibility path.
