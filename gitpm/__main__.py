import sys

if len(sys.argv) > 1 and sys.argv[1] == "triage":  # used by the GitHub Action
    import json
    from .server import STORE
    from . import ai
    print(json.dumps(ai.triage(STORE), indent=2))
elif len(sys.argv) > 1 and sys.argv[1] in ("standup", "sprint", "risks"):
    from .server import STORE
    from . import ai
    print(ai.brief(STORE, sys.argv[1]))
else:
    from .server import main
    main()
