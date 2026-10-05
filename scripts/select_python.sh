#!/usr/bin/env bash

applyrx_python_supported() {
  local candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

applyrx_python_error() {
  echo "Applyrx build requires Python 3.10 or newer" >&2
  echo "Install Python 3.10–3.13, then rerun this command. The system Python will not be modified." >&2
}

applyrx_select_python() {
  local candidate

  if [[ -n "${APPLYRX_PYTHON:-}" ]]; then
    if command -v "$APPLYRX_PYTHON" >/dev/null 2>&1; then
      candidate="$(command -v "$APPLYRX_PYTHON")"
    elif [[ -x "$APPLYRX_PYTHON" ]]; then
      candidate="$APPLYRX_PYTHON"
    else
      applyrx_python_error
      echo "APPLYRX_PYTHON does not name an executable interpreter." >&2
      return 1
    fi
    if ! applyrx_python_supported "$candidate"; then
      applyrx_python_error
      return 1
    fi
    printf '%s\n' "$candidate"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    candidate="$(command -v python3)"
    if applyrx_python_supported "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  for candidate_name in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate_name" >/dev/null 2>&1; then
      candidate="$(command -v "$candidate_name")"
      if applyrx_python_supported "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done

  applyrx_python_error
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  applyrx_select_python
fi
