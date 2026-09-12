"""Build the auto-submitting HTML form used to emulate a POST navigation.

Shared by both engines so a `request.post` behaves identically whether solved by
Chrome/Selenium or Camoufox/Playwright. Ported from FlareSolverr's original
`_post_request`.

**Both engines navigate to this markup as a `data:text/html,` URL**, which is why
every field is percent-encoded with `quote()` and not merely HTML-escaped. The
browser URL-decodes the whole document before the HTML parser ever sees it, so a
value carrying a bare `%` or `#` would otherwise be re-read as an escape sequence
or truncate the document at the fragment. Measured against a live echo service: a
value of `100% off` and one of `a#b` both survive with `quote()` and neither does
without it. That decode is also what undoes the encoding again, so the field
reaches the server as the caller wrote it. The action URL needs the same
treatment: it used to be escaped only, so a URL carrying a `%` or a `#` was
re-read by that decode and the form posted somewhere else.
"""
from html import escape
from urllib.parse import unquote, quote


def _attr(text: str) -> str:
    """`text` as an attribute value that survives the browser's URL decode.

    HTML-escaped first and percent-encoded second, because that is the order the
    browser undoes them in: it URL-decodes the whole document, and only then
    parses the markup. Escaping after encoding leaves a quote written as %22,
    which the decode turns back into a quote before the parser sees it, and that
    closes the attribute.
    """
    return quote(escape(text, quote=True))


def build_post_html(url: str, post_data: str) -> str:
    post_form = f'<form id="hackForm" action="{_attr(url)}" method="POST">'
    query_string = post_data if post_data and post_data[0] != '?' else post_data[1:] if post_data else ''
    pairs = query_string.split('&')
    for pair in pairs:
        parts = pair.split('=', 1)
        # noinspection PyBroadException
        try:
            name = unquote(parts[0])
        except Exception:
            name = parts[0]
        if name == 'submit':
            continue
        # noinspection PyBroadException
        try:
            value = unquote(parts[1]) if len(parts) > 1 else ''
        except Exception:
            value = parts[1] if len(parts) > 1 else ''
        post_form += f'<input type="text" name="{_attr(name)}" value="{_attr(value)}"><br>'
    post_form += '</form>'
    return f"""
        <!DOCTYPE html>
        <html>
        <body>
            {post_form}
            <script>document.getElementById('hackForm').submit();</script>
        </body>
        </html>"""
