"""Build the auto-submitting HTML form used to emulate a POST navigation.

Shared by both engines so a `request.post` behaves identically whether solved by
Chrome/Selenium or Camoufox/Playwright. Ported verbatim from FlareSolverr's
original `_post_request`.
"""
from html import escape
from urllib.parse import unquote, quote


def build_post_html(url: str, post_data: str) -> str:
    post_form = f'<form id="hackForm" action="{url}" method="POST">'
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
