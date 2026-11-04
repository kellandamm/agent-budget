"""Plugin entry point: use an explicitly selected, previously indexed workspace."""

import os
from pathlib import Path
import sys

import agent_budget


def main():
    root = os.environ.get("AGENT_BUDGET_ROOT", "")
    if not root or not Path(root).is_absolute():
        print(
            "Agent Budget: set AGENT_BUDGET_ROOT to an absolute workspace path "
            "and index that workspace before starting the host.",
            file=sys.stderr,
        )
        return 1
    arguments = ["agent-budget", "--root", root]
    state = os.environ.get("AGENT_BUDGET_STATE")
    if state:
        if not Path(state).is_absolute():
            print("Agent Budget: AGENT_BUDGET_STATE must be absolute.", file=sys.stderr)
            return 1
        arguments.extend(["--state", state])
    arguments.append("serve")
    sys.argv = arguments
    return agent_budget.main()


if __name__ == "__main__":
    raise SystemExit(main())
