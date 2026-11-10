"""A tiny example service with an internal module dependency."""

from .settings import ServiceSettings


class Service:
    def __init__(self, settings: ServiceSettings | None = None) -> None:
        self.settings = settings or ServiceSettings()

    def health(self) -> dict[str, str]:
        return {"status": "ok", "service": self.settings.service_name}
