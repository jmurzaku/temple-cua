"""Read a credential without echo and report only model-access metadata."""
import json
import re
import sys
import termios

import httpx


def read_key():
    previous = None
    if sys.stdin.isatty():
        previous = termios.tcgetattr(sys.stdin.fileno())
        hidden = previous.copy()
        hidden[3] &= ~termios.ECHO
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, hidden)
    try:
        print("Ready for credential on stdin (echo disabled).", flush=True)
        return sys.stdin.readline().strip()
    finally:
        if previous is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, previous)


def main():
    key = read_key()
    if not key:
        print(json.dumps({"status": "missing_credential"}))
        return 1
    try:
        response = httpx.get("https://api.openai.com/v1/models",
                             headers={"Authorization": "Bearer " + key}, timeout=30)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            print(json.dumps({"http_status": response.status_code,
                              "error_code": error.get("code") if isinstance(error, dict) else None,
                              "error_type": error.get("type") if isinstance(error, dict) else None}))
            return 1
        ids = sorted(x["id"] for x in data.get("data", []) if isinstance(x, dict) and isinstance(x.get("id"), str))
        matching = [x for x in ids if re.search(r"^gpt-(?:6(?:[.-]1)?(?:$|-)|.*sol$)", x, re.I)]
        print(json.dumps({"http_status": response.status_code, "matching_model_ids": matching,
                          "available_model_count": len(ids)}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "request_error", "exception_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
