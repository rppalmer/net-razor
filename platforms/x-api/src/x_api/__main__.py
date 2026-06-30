from __future__ import annotations

import uvicorn

from x_api.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "x_api.main:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
