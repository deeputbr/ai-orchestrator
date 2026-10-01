"""One request per invocation. Unknown outcomes are not retried automatically."""
import json
import socket
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from orchestrator.adapters.base import AdapterError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonTransport:
    def __init__(self, timeout=45):
        self.timeout = timeout

    def post(self, url, key, payload):
        if not key:
            raise AdapterError("missing_credentials")
        request = Request(url, data=json.dumps(payload, allow_nan=False).encode(),
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                data = response.read(4_000_001)
                if len(data) > 4_000_000:
                    raise AdapterError("response_too_large", uncertain=True)
                return json.loads(data)
        except HTTPError as exc:
            # Do not expose response body or request headers. No replay of 429/5xx here.
            raise AdapterError("http_error", status=exc.code, uncertain=exc.code >= 500 or exc.code == 408) from None
        except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
            raise AdapterError("transport_outcome_unknown", uncertain=True) from None
        except (ValueError, UnicodeError):
            raise AdapterError("invalid_json", uncertain=True) from None
