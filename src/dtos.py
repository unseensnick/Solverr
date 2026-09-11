import logging

STATUS_OK = "ok"
STATUS_ERROR = "error"


class ChallengeResolutionResultT:
    url: str = None
    status: int = None
    headers: list = None
    response: str = None
    # Only emitted when 'response' is not HTML (e.g. "application/pdf" with a
    # base64 body); absent means text/html, as in FlareSolverr.
    contentType: str = None
    cookies: list = None
    userAgent: str = None
    screenshot: str | None = None
    turnstile_token: str = None

    def __init__(self, _dict):
        self.__dict__.update(_dict)


class ChallengeResolutionT:
    status: str = None
    message: str = None
    result: ChallengeResolutionResultT = None

    def __init__(self, _dict):
        self.__dict__.update(_dict)
        if self.result is not None:
            self.result = ChallengeResolutionResultT(self.result)


class V1RequestBase(object):
    # V1RequestBase
    cmd: str = None
    cookies: list = None
    maxTimeout: int = None
    proxy: dict = None
    session: str = None
    session_ttl_minutes: int = None
    headers: list = None  # deprecated v2.0.0, not used
    userAgent: str = None  # deprecated v2.0.0, not used

    # V1Request
    url: str = None
    postData: str = None
    returnOnlyCookies: bool = None
    returnScreenshot: bool = None
    download: bool = None   # deprecated v2.0.0, not used
    returnRawHtml: bool = None  # deprecated v2.0.0, not used
    waitInSeconds: int = None
    # Optional resource blocking flag (blocks images, CSS, and fonts)
    disableMedia: bool = None
    # Optional when you've got a turnstile captcha that needs to be clicked after X number of Tab presses
    tabs_till_verify : int = None
    # Optional engine override: 'chrome' (Selenium/undetected_chromedriver),
    # 'stealth' (Camoufox/playwright-captcha), or 'auto'. Defaults to DEFAULT_ENGINE env.
    engine: str = None

    def __init__(self, _dict):
        self.__dict__.update(_dict)


# The annotations above are documentation: __init__ copies whatever JSON arrived,
# so nothing enforces them. This is what makes them binding, checked once at the
# /v1 boundary by validate_request_types.
#
# Derived from __annotations__ rather than restated, so a parameter added to the
# class above is checked without anyone remembering to add it here. Only the
# exceptions are listed.
_TYPE_OVERRIDES = {
    # Coerced rather than type-checked: a numeric string has always been accepted
    # and reaches the budget through int(), so refusing one now would break
    # callers that work today. _validate_max_timeout owns this parameter.
    'maxTimeout': None,
    # A fractional wait is meaningful and both engines sleep on it happily.
    'waitInSeconds': (int, float),
    # Deprecated in FlareSolverr v2 and never read by Solverr; upstream
    # FlareSolverr only warns and ignores it. Legacy clients such as Prowlarr
    # still send an object here (its HeadersPost), which the `list` annotation
    # above would reject outright. Exempt it rather than break a client that
    # isn't wrong.
    'headers': None,
}

_TYPE_NAMES = {
    str: 'a string',
    int: 'a whole number',
    bool: 'true or false',
    list: 'a list',
    dict: 'an object',
    (int, float): 'a number',
}


def _type_name(expected) -> str:
    """A readable name for a declared type, tuple included."""
    named = _TYPE_NAMES.get(expected)
    return named if named else 'a %s' % getattr(expected, '__name__', 'valid value')


def validate_request_types(req: 'V1RequestBase') -> None:
    """Refuse a parameter whose type the rest of the service cannot work with.

    Everything downstream reads these values with `in`, truthiness, comparison
    or indexing, none of which check what they were given. Three ways that bit
    before this existed: a string in a boolean parameter is truthy, so
    `returnOnlyCookies: "false"` dropped the response body; `url` reached a
    regex that raised `expected string or bytes-like object` naming a Python
    type rather than the parameter; and `waitInSeconds` failed on a comparison
    *after* the challenge was solved, throwing away a completed solve.

    Unknown parameters are logged and kept, never refused: the /v1 contract
    takes additive optional fields, and a client sending one this build does not
    know about must keep working.
    """
    for name, declared in V1RequestBase.__annotations__.items():
        expected = _TYPE_OVERRIDES.get(name, declared)
        if expected is None:
            continue
        value = getattr(req, name, None)
        if value is None:
            continue
        # bool is a subclass of int, so an int parameter has to exclude it
        # explicitly or `True` passes as 1.
        wants_number = expected is int or expected == (int, float)
        if isinstance(value, bool) and wants_number or not isinstance(value, expected):
            raise Exception("Request parameter '%s' must be %s." % (name, _type_name(expected)))

    unknown = sorted(set(req.__dict__) - set(V1RequestBase.__annotations__))
    if unknown:
        logging.warning("Ignoring unknown request parameter(s): %s. Check the spelling; "
                        "a misspelled parameter has no effect.", ", ".join(unknown))


class V1ResponseBase(object):
    # V1ResponseBase
    status: str = None
    message: str = None
    session: str = None
    sessions: list[str] = None
    startTimestamp: int = None
    endTimestamp: int = None
    version: str = None

    # V1ResponseSolution
    solution: ChallengeResolutionResultT = None

    # hidden vars
    __error_500__: bool = False

    def __init__(self, _dict):
        self.__dict__.update(_dict)
        if self.solution is not None:
            self.solution = ChallengeResolutionResultT(self.solution)


class IndexResponse(object):
    msg: str = None
    version: str = None
    userAgent: str = None

    def __init__(self, _dict):
        self.__dict__.update(_dict)


class HealthResponse(object):
    status: str = None

    def __init__(self, _dict):
        self.__dict__.update(_dict)
