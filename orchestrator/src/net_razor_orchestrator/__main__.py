from __future__ import annotations

import uvicorn

from net_razor_orchestrator.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "net_razor_orchestrator.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
