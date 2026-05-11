"""Custom exceptions for the monitor system."""


class MonitorError(Exception):
    """Base exception."""


class SessionExpiredError(MonitorError):
    """All user sessions have expired."""


class CollectionError(MonitorError):
    """Failed to collect data from a source."""


class ParseError(MonitorError):
    """Failed to parse content from a source."""


class NotificationError(MonitorError):
    """Failed to send a notification."""


class ConfigError(MonitorError):
    """Invalid configuration."""
