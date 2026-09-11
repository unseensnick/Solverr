"""Keep credentials out of the logs.

The controller logs whole /v1 requests and responses, a habit inherited from FlareSolverr, and
the Chrome launch logs the proxy URL. These helpers keep those lines useful (the command, the URL,
which cookies, headers and form fields there were) while replacing the values that work as
credentials: a proxy password, cookie values, Cookie and Set-Cookie headers, form field values,
and the Turnstile token.
"""
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"

_SECRET_KEYS = {"password", "turnstile_token"}
_COOKIE_HEADERS = {"cookie", "set-cookie"}


def url(value):
    """`value` with the password in its userinfo replaced; anything else comes back unchanged."""
    if not isinstance(value, str):
        return value
    try:
        parts = urlsplit(value)
        has_password = parts.password is not None
    except ValueError:
        return value
    if not has_password:
        return value
    userinfo, _, hostport = parts.netloc.rpartition("@")
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc="%s:%s@%s" % (user, REDACTED, hostport)))


def form(value):
    """A urlencoded body with every field name kept and every value replaced."""
    if not isinstance(value, str) or not value:
        return value
    return "&".join(pair.split("=", 1)[0] + "=" + REDACTED if "=" in pair else pair
                    for pair in value.split("&"))


def body(obj):
    """A copy of a logged /v1 request or response dict, credential values replaced."""
    if isinstance(obj, list):
        return [body(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for key, value in obj.items():
        if key in _SECRET_KEYS and value:
            out[key] = REDACTED
        elif key == "cookies" and isinstance(value, list):
            out[key] = [dict(c, value=REDACTED) if isinstance(c, dict) and "value" in c else c
                        for c in value]
        elif key == "headers" and isinstance(value, dict):
            out[key] = {k: REDACTED if str(k).lower() in _COOKIE_HEADERS else v for k, v in value.items()}
        elif key == "postData":
            out[key] = form(value)
        elif key == "url":
            out[key] = url(value)
        else:
            out[key] = body(value)
    return out
