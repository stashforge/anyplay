"""Translate standalone companion setup errors for the media application's owner."""

from ...core.api import ProviderDependencyError
from .client import CompanionSetupRequired


class CompanionDependencyError(CompanionSetupRequired, ProviderDependencyError):
    """Keep transport compatibility while using the CLI dependency-error lifecycle."""


def companion_dependency_error(error: CompanionSetupRequired) -> ProviderDependencyError:
    if isinstance(error, ProviderDependencyError):
        return error
    return CompanionDependencyError(str(error))
