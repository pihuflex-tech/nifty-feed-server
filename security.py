"""
Security and Credential Sanitization Module
Prevents sensitive authentication credentials from appearing in logs, exception traces, or error objects.
"""

import re
import logging
from typing import Any, Dict

# Sensitive parameter keys to scrub from dictionaries and headers
SENSITIVE_KEYS = {
    "api_key",
    "privatekey",
    "x-privatekey",
    "password",
    "totp",
    "pin",
    "clientcode",
    "client_code",
    "jwttoken",
    "feedtoken",
    "refreshtoken",
    "access_token",
    "authorization",
}

# Regex patterns for scrubbing string log messages (Defense-in-depth)
SENSITIVE_PATTERNS = [
    (re.compile(r"([\'\"]?X-PrivateKey[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?password[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?totp[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?clientcode[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?jwtToken[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?feedToken[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?refreshToken[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
    (re.compile(r"([\'\"]?Authorization[\'\"]?\s*:\s*[\'\"])(?:[^\'\"]+)([\'\"])", re.IGNORECASE), r"\g<1>[REDACTED]\g<2>"),
]


def sanitize_data(data: Any) -> Any:
    """Recursively redacts sensitive values from dictionaries, lists, or tuples."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower().replace("-", "_")
            if any(secret_key in k_lower for secret_key in SENSITIVE_KEYS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(sanitize_data(item) for item in data)
    return data


def redact_string(message: str) -> str:
    """Scans and scrubs sensitive patterns from string messages."""
    if not isinstance(message, str):
        return message
    result = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts credentials and tokens from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_data(record.args)
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    redact_string(a) if isinstance(a, str) else sanitize_data(a)
                    for a in record.args
                )
        return True


def patch_smartconnect_logging() -> bool:
    """
    Patches SmartConnect._request in smartapi-python to ensure request bodies
    and headers are sanitized BEFORE any error message is constructed or logged.
    """
    try:
        try:
            from SmartApi.smartConnect import SmartConnect
        except ImportError:
            from smartapi.smartConnect import SmartConnect
    except ImportError:
        return False

    orig_request = getattr(SmartConnect, "_request", None)
    if not orig_request or getattr(SmartConnect, "_is_security_patched", False):
        return True

    def safe_request(self, route, method, parameters=None):
        import json
        import requests
        from urllib.parse import urljoin
        from logzero import logger
        import SmartApi.smartExceptions as ex

        params = parameters.copy() if parameters else {}
        uri = self._routes[route].format(**params)
        url = urljoin(self.root, uri)
        headers = self.requestHeaders()

        if self.access_token:
            auth_header = self.access_token
            headers["Authorization"] = f"Bearer {auth_header}"

        # Sanitize for any logging output
        safe_headers = sanitize_data(headers)
        safe_params = sanitize_data(params)

        if self.debug:
            logger.debug(f"Request: {method} {url} {safe_params} {safe_headers}")

        try:
            r = requests.request(
                method,
                url,
                data=json.dumps(params) if method in ["POST", "PUT"] else None,
                params=json.dumps(params) if method in ["GET", "DELETE"] else None,
                headers=headers,
                verify=not self.disable_ssl,
                allow_redirects=True,
                timeout=self.timeout,
                proxies=self.proxies,
            )
        except Exception as e:
            # Crucial: log only SAFE headers and parameters
            logger.error(
                f"Error occurred while making a {method} request to {url}. "
                f"Headers: {safe_headers}, Request: {safe_params}, Response: {type(e).__name__}: {e}"
            )
            raise e

        if self.debug:
            logger.debug(f"Response: {r.status_code} {r.content}")

        if "json" in headers.get("Content-type", ""):
            try:
                data = json.loads(r.content.decode("utf8"))
            except ValueError:
                raise ex.DataException(f"Couldn't parse JSON response: {r.content}")

            if data.get("error_type"):
                if self.session_expiry_hook and r.status_code == 403 and data["error_type"] == "TokenException":
                    self.session_expiry_hook()
                exp = getattr(ex, data["error_type"], ex.GeneralException)
                raise exp(data.get("message", "API Error"), code=r.status_code)

            if data.get("status", False) is False:
                safe_response = sanitize_data(data)
                logger.error(
                    f"Error occurred while making a {method} request to {url}. "
                    f"Error: {data.get('message', 'Unknown')}. URL: {url}, "
                    f"Headers: {safe_headers}, Request: {safe_params}, Response: {safe_response}"
                )
            return data
        elif "csv" in headers.get("Content-type", ""):
            return r.content
        else:
            raise ex.DataException(f"Unknown Content-type with response: {r.content}")

    SmartConnect._request = safe_request
    SmartConnect._is_security_patched = True
    return True


def configure_safe_logging(log_level: int = logging.INFO) -> None:
    """
    Initializes safe logging across the system:
    1. Installs SensitiveDataFilter on logzero and root loggers.
    2. Wraps logzero.logfile to auto-attach filters to all file handlers.
    3. Patches SmartConnect to prevent raw credentials from being passed to loggers.
    """
    sensitive_filter = SensitiveDataFilter()

    # 1. Attach to root logger
    root_logger = logging.getLogger()
    if not any(isinstance(f, SensitiveDataFilter) for f in root_logger.filters):
        root_logger.addFilter(sensitive_filter)

    # 2. Configure logzero if present
    try:
        import logzero

        if not any(isinstance(f, SensitiveDataFilter) for f in logzero.logger.filters):
            logzero.logger.addFilter(sensitive_filter)

        # Intercept logzero.logfile to auto-add filter on newly attached file handlers
        # and enforce strict size bounds (512 KB) to prevent Android storage exhaustion
        orig_logfile = logzero.logfile

        def safe_logfile(filename, *args, **kwargs):
            if "maxBytes" not in kwargs:
                kwargs["maxBytes"] = 512 * 1024  # 512 KB max log file size
            if "backupCount" not in kwargs:
                kwargs["backupCount"] = 1         # 1 backup file max
            orig_logfile(filename, *args, **kwargs)
            for handler in logzero.logger.handlers:
                if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
                    handler.addFilter(SensitiveDataFilter())

        logzero.logfile = safe_logfile

    except ImportError:
        pass

    # 3. Patch SmartConnect._request
    patch_smartconnect_logging()
