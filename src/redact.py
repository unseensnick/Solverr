"""Keep credentials out of the logs.

The controller logs whole /v1 requests and responses, a habit inherited from FlareSolverr, and
the Chrome launch logs the proxy URL. These helpers keep those lines useful (the command, the URL,
which cookies, headers and form fields there were) while replacing the values that work as
credentials: a proxy password, cookie values, Cookie and Set-Cookie headers, form field values,
and the Turnstile token.
"""
from urllib.parse import quote, urlsplit, urlunsplit

REDACTED = "<redacted>"

_SECRET_KEYS = {"password", "turnstile_token"}
_SECRET_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}


def url(value):
    """`value` with the password in its userinfo replaced; anything else comes back unchanged."""
    if not isinstance(value, str):
        return value
    try:
        parts = urlsplit(value)
        has_password = parts.password is not None
    except ValueError:
        # A URL that will not parse may still carry a password, so it is the one
        # case where the whole value has to go rather than come back as it was.
        return REDACTED
    if not has_password:
        return value
    userinfo, _, hostport = parts.netloc.rpartition("@")
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc="%s:%s@%s" % (user, REDACTED, hostport)))


def proxy_url(value):
    """A proxy URL for a log line, with anything credential-shaped gone.

    Stricter than `url`, because this field's userinfo is the credential. A
    password carrying a "/", "?" or "#" ends urlsplit's authority early and a
    URL with no scheme has no authority at all, so the parse reports no password
    where there plainly is one, and `url` then hands the value back whole.
    Anything still shaped like userinfo after that goes whole rather than partly.
    """
    if not isinstance(value, str) or not value:
        return value
    replaced = url(value)
    if replaced != value:
        return replaced
    head = value.split("@", 1)[0]
    if "@" in value and ":" in head:
        return REDACTED
    return value


def _proxy(value):
    """The `proxy` field, whatever shape it arrived in.

    The one field whose whole purpose is to carry a credential, so a shape this
    does not recognise is replaced rather than walked: the request is logged
    before anything type-checks it, and a URL inside a list or a nested object
    reached the log intact.
    """
    if isinstance(value, str):
        return proxy_url(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _SECRET_KEYS and item:
                out[key] = REDACTED
            elif key == "url":
                out[key] = proxy_url(item) if isinstance(item, str) else REDACTED
            else:
                out[key] = body(item)
        return out
    if value in (None, "", [], {}):
        return value
    return REDACTED


def proxy_text(value, proxy_config):
    """Text we did not write (a library's exception message) with a proxy's
    credentials taken out.

    An egress lookup that fails quotes back the URL it built, which is the
    configured server with the username and password spliced in, so the message
    carries both even when the configured server does not.
    """
    if not isinstance(value, str) or not proxy_config:
        return value
    server = proxy_config.get("server")
    password = proxy_config.get("password")
    secrets = [password, _password_in(server)]
    if isinstance(password, str) and password:
        # invisible_core builds its URL with quote(password, safe=''), so the
        # message carries the encoded form, which a literal replace would miss.
        secrets += [quote(password, safe=""), quote(password)]
    for secret in secrets:
        if secret:
            value = value.replace(secret, REDACTED)
    if isinstance(server, str) and server:
        value = value.replace(server, url(server))
    return value


def _password_in(value):
    """The password in a URL's userinfo, or None."""
    if not isinstance(value, str):
        return None
    try:
        return urlsplit(value).password
    except ValueError:
        return None


def form(value):
    """A urlencoded body with every field name kept and every value replaced."""
    if not isinstance(value, str) or not value:
        return value
    return "&".join(pair.split("=", 1)[0] + "=" + REDACTED if "=" in pair else pair
                    for pair in value.split("&"))


def _cookies(value):
    """Cookie values gone, whatever shape the client sent them in."""
    if isinstance(value, list):
        return [dict(c, value=REDACTED) if isinstance(c, dict) and "value" in c
                else c if isinstance(c, dict) else REDACTED
                for c in value]
    if isinstance(value, dict):
        return {k: REDACTED for k in value}
    if isinstance(value, str):
        return REDACTED
    return value


def _headers(value):
    """Header values that work as credentials gone; an unknown shape goes whole.

    The field is deprecated and never read, so there is nothing to lose by
    replacing an unexpected shape outright, and an Authorization value can sit
    anywhere inside one.
    """
    if isinstance(value, dict):
        return {k: REDACTED if str(k).lower() in _SECRET_HEADERS else v
                for k, v in value.items()}
    if value in (None, "", [], {}):
        return value
    return REDACTED


def _post_data(value):
    """Form values gone, whether the body came as a string or as an object."""
    if isinstance(value, str):
        return form(value)
    if isinstance(value, dict):
        return {k: REDACTED for k in value}
    if isinstance(value, list):
        return REDACTED
    return value


def body(obj):
    """A copy of a logged /v1 request or response dict, credential values replaced.

    This runs before the request boundary has type-checked anything, so every
    field can arrive in a shape the validator is about to refuse. Each rule below
    therefore says what to do with the shapes it does not expect, rather than
    letting them through: the value is logged either way, and only then refused.
    """
    if isinstance(obj, list):
        return [body(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for key, value in obj.items():
        if key in _SECRET_KEYS and value:
            out[key] = REDACTED
        elif key == "cookies":
            out[key] = _cookies(value)
        elif key == "headers":
            out[key] = _headers(value)
        elif key == "postData":
            out[key] = _post_data(value)
        elif key == "proxy":
            out[key] = _proxy(value)
        elif key == "url":
            out[key] = url(value)
        else:
            out[key] = body(value)
    return out
