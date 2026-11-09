"""One public/Shadow GET in a killable process. Protocol is private pipe only."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    config = json.loads(sys.stdin.buffer.read())
    request = Request(config['url'], headers={
        'User-Agent': 'taoli1-fill-sim/1', **(config['headers'] or {})}, method='GET')
    try:
        with build_opener(NoRedirect()).open(request, timeout=config['timeout']) as response:
            if response.status != 200:
                raise ValueError('http_non_200')
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError('response_too_large')
        sys.stdout.buffer.write(b'OK\n' + body)
    except HTTPError as exc:
        sys.stdout.buffer.write(b'HTTP\n' + json.dumps(dict(code=exc.code, headers={
            'Retry-After': exc.headers.get('Retry-After', '0')})).encode())
    except Exception:
        # Never echo the request URL, headers, token or untrusted response body.
        sys.stdout.buffer.write(b'ERROR\n')


if __name__ == '__main__':
    main()
