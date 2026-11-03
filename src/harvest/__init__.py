"""Public compatibility exports, loaded only when requested by a handler."""

from importlib import import_module

_EXPORTS = {
    "Auth": ("harvest.auth", "Auth"),
    "Project": ("harvest.project", "Project"),
    "ProjectUser": ("harvest.project_user", "ProjectUser"),
    "Work": ("harvest.work", "Work"),
    "Generate": ("harvest.generate", "Generate"),
    "Template": ("harvest.template", "Template"),
    "Notification": ("harvest.notification", "Notification"),
    "Corporation": ("harvest.corporation", "Corporation"),
    "RequestDecorator": ("harvest.utils.request_decorator", "RequestDecorator"),
    "ActionDeniedError": ("harvest.exception", "ActionDeniedError"),
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
