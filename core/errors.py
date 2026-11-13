class DomainError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


class ValidationError(DomainError):
    pass


class ServiceError(DomainError):
    """Application-service failure with a stable code for the UI."""


class NotFoundError(ServiceError):
    pass


class InvalidStateError(ServiceError):
    pass
