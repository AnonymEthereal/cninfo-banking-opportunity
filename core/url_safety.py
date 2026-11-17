from urllib.parse import urlsplit


def safe_https_url(value: str | None) -> str | None:
    """Return a safe absolute HTTPS URL, or None for an invalid link."""
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        return None
    candidate = value
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        # Accessing .port also validates malformed port values.
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return candidate
