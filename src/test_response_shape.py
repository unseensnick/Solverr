"""Browser-free tests for the shape of a solved response.

Covers the two things that are easy to break silently: the /v1 payload for an
ordinary HTML solve must stay byte-identical to FlareSolverr's (no extra keys),
and a non-HTML document must survive the trip through the passthrough as bytes.

Run: PYTHONPATH=src uv run --no-project python -m unittest test_response_shape
"""
import base64
import unittest
from unittest.mock import patch

import flaresolverr_service
import passthrough
import utils
from dtos import STATUS_OK, V1ResponseBase
from engines.base import SolveResult

PDF_BYTES = b"%PDF-1.4 fake document"


def _serialized(result: SolveResult) -> dict:
    return utils.object_to_dict(flaresolverr_service._to_challenge_resolution(result))['result']


def _solver_response(response: str, content_type: str = None) -> V1ResponseBase:
    solution = {"url": "https://example.tld/doc", "status": 200, "response": response}
    if content_type is not None:
        solution["contentType"] = content_type
    return V1ResponseBase({"status": STATUS_OK, "solution": solution})


class HtmlResponseShapeTest(unittest.TestCase):

    def test_html_solve_omits_content_type(self):
        self.assertNotIn('contentType', _serialized(SolveResult(response="<html/>")))


class PdfResponseShapeTest(unittest.TestCase):

    def test_pdf_solve_reports_its_content_type(self):
        result = SolveResult(response="ZmFrZQ==", content_type="application/pdf")
        self.assertEqual(_serialized(result)['contentType'], "application/pdf")


class PassthroughBodyTest(unittest.TestCase):

    def test_pdf_solution_is_served_as_raw_bytes(self):
        encoded = base64.b64encode(PDF_BYTES).decode("ascii")
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response(encoded, "application/pdf")):
            _status, body, _content_type, _solution = passthrough._solve("https://example.tld/doc")
        self.assertEqual(body, PDF_BYTES)

    def test_pdf_solution_is_served_under_its_content_type(self):
        encoded = base64.b64encode(PDF_BYTES).decode("ascii")
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response(encoded, "application/pdf")):
            _status, _body, content_type, _solution = passthrough._solve("https://example.tld/doc")
        self.assertEqual(content_type, "application/pdf")

    def test_html_solution_stays_html(self):
        with patch.object(flaresolverr_service, 'controller_v1_endpoint',
                          return_value=_solver_response("<html/>")):
            _status, _body, content_type, _solution = passthrough._solve("https://example.tld/page")
        self.assertEqual(content_type, passthrough._HTML_CONTENT_TYPE)


if __name__ == '__main__':
    unittest.main()
