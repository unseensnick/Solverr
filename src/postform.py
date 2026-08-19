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
reaches the server as the caller wrote it.
"""
from html import escape
from urllib.parse import unquote, quote


def build_post_html(url: str, post_data: str) -> str:
    # Escaped: a quote in the URL used to close the attribute early, which sent
    # the form somewhere else and put the rest of the URL into the page as markup.
    post_form = f'<form id="hackForm" action="{escape(url, quote=True)}" method="POST">'
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
        # Protection of " character, for syntax
        value = value.replace('"', '&quot;')
        post_form += f'<input type="text" name="{escape(quote(name))}" value="{escape(quote(value))}"><br>'
    post_form += '</form>'
    return f"""
        <!DOCTYPE html>
        <html>
        <body>
            {post_form}
            <script>document.getElementById('hackForm').submit();</script>
        </body>
        </html>"""
