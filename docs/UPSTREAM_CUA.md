# Proposed Cua contribution

An upstream contribution is worthwhile, but the TempleOS project does not need
to wait for it. Its pinned compatibility adapter already works around the
general defects. No upstream issue, comment, branch push, or pull request has
been created.

Suggested focused title: `fix(agent): dispatch OpenAI computer function calls`.

## Audited versions

Base commit: [`033b980cd2d1ed744309843cfa8fe3c18ca368e6`](https://github.com/trycua/cua/commit/033b980cd2d1ed744309843cfa8fe3c18ca368e6).
The audited OpenAI loop, agent runner, generic loop, action normalizer and custom
handler files are byte-identical to the installed PyPI `cua-agent==0.9.0`.
The problems are therefore still present in that main-branch snapshot.

## Smallest useful OpenAI fix

The already-supported `openai/gpt-5.4` loop advertises a standard function tool
named `computer`, then returns its raw `function_call` to `ComputerAgent`.
The agent's function dispatcher cannot resolve that name from a custom-computer
dictionary or handler. Input is not delivered, and subsequent model calls do
not receive the intended screenshot result.

The local candidate modifies only the existing OpenAI loop and adds a hermetic
test file. It:

- converts the loop's computer function calls into Cua computer actions;
- converts Cua action history back to Responses function calls and outputs,
  with screenshots in separate visible image messages;
- projects the upstream flat tool's documented inactive fields away, including
  non-null defaults, while validating active fields;
- resolves custom-computer dictionaries through Cua's existing handler factory
  so their dimensions and environment reach the tool schema;
- preserves native computer-use-preview behavior and unrelated function calls.

The candidate does not add an OpenAI model alias, broaden routing, include a
TempleOS driver, or add TempleOS ASCII, coordinate or scrolling restrictions.
Support for additional generic OpenAI model IDs should be discussed separately
from repairing the existing GPT-5.4 contract.

## Reproduction and validation

The new focused tests reproduce failures against unmodified base source:
**7 failed, 1 passed**. With the candidate: **8 passed**.
The main regression executes the real `ComputerAgent` through three mocked
Responses calls, delivering Unicode text and Enter, then receiving a final
message. It verifies exact model routing, full flat-schema defaults, valid
second-turn function history, visible screenshots and token usage.

Command run from the Cua checkout, using the project's existing SDK dependency
environment and isolated review tools without changing its pinned installation:

```sh
CUA_TELEMETRY_ENABLED=false \
PYTHONPATH=libs/python/agent:/tmp/cua-review-tools \
/workspace/temple-cua/.venv/bin/python -m pytest \
  libs/python/agent/tests/test_openai_function_computer.py -q
```

Black and isort formatted both changed files; Ruff passed. No model API,
credentials, VM or Cua hosted service was used. Only the focused subset was run;
the full upstream suite and live mainstream-desktop certification remain to be
run before an upstream PR is considered ready.

The accompanying `openai-function-computer.patch` includes both the production
change and the complete new test file, against the exact base above.

## Separate general defects

These remain outside the proposed OpenAI patch:

- Image retention removes a screenshot by adjacency rather than `call_id`.
  A batched `[call(a), call(b), output(a), output(b)]` can become
  `[call(a), call(b), output(b)]`, orphaning the first call.
- Supported base64 screenshots do not populate inferred custom-computer
  dimensions; bytes do.
- Async callable objects and synchronous callbacks returning awaitables are
  not awaited by the custom handler.
- Malformed coordinate normalization can raise `IndexError`/`TypeError`, and
  action pruning discards an explicit wait `ms` argument.

Each merits its own small regression and fix. Missing input callbacks silently
doing nothing is explicitly documented; changing that behavior would be a
separate strict-mode feature, not an incidental bug fix.

Read-only duplicate checking found related open
[PR #4537](https://github.com/trycua/cua/pull/4537), which repairs replay in the
generic Qwen/VLM loop rather than this OpenAI Responses loop. GitHub search
requests returned HTTP 422; only the first 100 open issue/PR titles and that
related PR were inspected. Duplicate and active-work checking is not exhaustive
and must be repeated before submission.

Cua's contributing guide permits small self-contained focused PRs, but requires
clear scope and acceptance evidence. This artifact is a local proposal for user
review, not an upstream submission.
