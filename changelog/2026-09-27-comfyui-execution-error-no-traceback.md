# ComfyUI execution failures no longer quote the traceback to the model

Date: 2026-09-27 · follow-up to B10, raised at its review · Tier 2 (formats error text
only; no schema, no behaviour change beyond what reaches the model). Plan approved.

## What was wrong

- When a job failed inside ComfyUI, `_await_result` raised `ComfyUIGenerationFailed`
  quoting the first 400 characters of the raw `status.messages` record. That code dates
  from A1c.
- `registry.dispatch` turns the exception into `"{Type}: {message}"`, and that text goes
  to the model as the `TOOL_ERROR` result.
- ComfyUI's `execution_error` record (from `execution.py`'s `handle_execution_error`)
  carries a `traceback` from `traceback.format_tb()`: file paths under
  `/Volumes/Dock Storage/ComfyUI/...` and line numbers. It also carries
  `current_inputs`.
- 400 characters of the raw record reach into the traceback. Reproduced by the break
  test below: the old text ends `"traceback": ["  File \"/...`.

## What changed

`program/media/comfyui.py`:

- **New `_execution_failure(status)`** summarises the failure as
  `"<node_type> (node <id>) raised <exception_type>: <exception_message>"`.
  - The exception message is capped at 300 characters.
  - The `traceback` and `current_inputs` are dropped.
  - An `execution_interrupted` event reports only where it stopped.
  - A failure with no error event lists the event **names** only.
- **The full record goes to the log**, at WARNING, with the prompt id, so the operator
  keeps everything the exception text no longer carries.

## Tests

4 new, in `tests/test_comfyui.py`. They use a fixture in the real `execution_error`
shape, with a two-frame traceback of local paths and a marker in `current_inputs`.

- The exception text names the node, the exception type and the message. It contains no
  traceback path, no `execution.py`, no `/Volumes/`, no line number and no input marker.
  The log contains the traceback path and the input marker.
- The exception message is capped at 300 characters.
- An interrupted run says where it stopped, without quoting the record.
- A failure with no error event lists event names only.
- The existing `test_a_failed_execution_is_not_a_rejection` passes unchanged.

**Proven to bite:** restoring the raw 400-character dump fails all 4, and the leaked
traceback is visible in the failure output.

**Full suite:** 1,268 passed, 2 skipped (was 1,264). `ruff check .` is clean.

## Known limitations

- **`exception_message` is still quoted, as approved**, and it is ComfyUI's own text. An
  exception whose message itself names a file, such as a `FileNotFoundError` for a
  checkpoint, would still carry that path. The traceback, the systematic source of
  paths, is gone.
- Not observed live. Forcing a real in-graph failure on the live instance was not
  attempted; the fixture follows the fields `execution.py` writes in ComfyUI 0.37.0.
