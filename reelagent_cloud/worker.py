"""`reelagent-worker` 控制台入口。"""

import sys

from .workers.runner import main

if __name__ == "__main__":
    sys.exit(main())
