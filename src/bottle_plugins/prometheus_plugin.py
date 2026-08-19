import logging
import os
import threading
import urllib.parse

from bottle import request
from dtos import V1RequestBase, V1ResponseBase
from metrics import start_metrics_http_server, REQUEST_COUNTER, REQUEST_DURATION

PROMETHEUS_ENABLED = os.environ.get('PROMETHEUS_ENABLED', 'false').lower() == 'true'
PROMETHEUS_PORT = int(os.environ.get('PROMETHEUS_PORT', 8192))


# Prometheus holds one time series per distinct label value for the life of the
# process and nothing here evicts, so labelling by hostname let the registry grow
# with the number of hosts a client asked for. Past this many distinct hosts every
# further one is reported as _OTHER_DOMAIN: a deployer running a handful of
# indexers keeps full per-domain data and never reaches the cap, while a broad
# workload degrades to one bucket instead of growing without bound.
_MAX_DOMAIN_LABELS = 100
_OTHER_DOMAIN = 'other'
_UNKNOWN_DOMAIN = 'unknown'

_seen_domains = set()
_domains_lock = threading.Lock()


def reset_domain_labels() -> None:
    """Forget every domain seen so far. Exists for tests."""
    with _domains_lock:
        _seen_domains.clear()


def parse_domain_url(url) -> str:
    """The metric label for ``url``: its hostname, or a sentinel.

    Returns _OTHER_DOMAIN once _MAX_DOMAIN_LABELS distinct hosts have been seen,
    and _UNKNOWN_DOMAIN when the URL carries no hostname, which would otherwise
    label a series "None".
    """
    hostname = urllib.parse.urlparse(url).hostname if url else None
    if not hostname:
        return _UNKNOWN_DOMAIN
    with _domains_lock:
        if hostname in _seen_domains:
            return hostname
        if len(_seen_domains) >= _MAX_DOMAIN_LABELS:
            return _OTHER_DOMAIN
        _seen_domains.add(hostname)
    return hostname


def setup():
    if PROMETHEUS_ENABLED:
        start_metrics_http_server(PROMETHEUS_PORT)


def prometheus_plugin(callback):
    """
    Bottle plugin to expose Prometheus metrics
    https://bottlepy.org/docs/dev/plugindev.html
    """
    def wrapper(*args, **kwargs):
        actual_response = callback(*args, **kwargs)

        if PROMETHEUS_ENABLED:
            try:
                export_metrics(actual_response)
            except Exception as e:
                logging.warning("Error exporting metrics: " + str(e))

        return actual_response

    def export_metrics(actual_response):
        res = V1ResponseBase(actual_response)

        if res.startTimestamp is None or res.endTimestamp is None:
            # skip management and healthcheck endpoints
            return

        domain = _UNKNOWN_DOMAIN
        if res.solution and res.solution.url:
            domain = parse_domain_url(res.solution.url)
        else:
            # timeout error
            req = V1RequestBase(request.json)
            if req.url:
                domain = parse_domain_url(req.url)

        run_time = (res.endTimestamp - res.startTimestamp) / 1000
        REQUEST_DURATION.labels(domain=domain).observe(run_time)

        result = "unknown"
        if res.message == "Challenge solved!":
            result = "solved"
        elif res.message == "Challenge not detected!":
            result = "not_detected"
        elif res.message.startswith("Error"):
            result = "error"
        REQUEST_COUNTER.labels(domain=domain, result=result).inc()

    return wrapper
