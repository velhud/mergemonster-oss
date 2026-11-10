"""Synthetic local settings used by the MergeMonster example."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceSettings:
    service_name: str = "example-service"
    timeout_seconds: int = 3
