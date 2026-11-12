"""Optional authenticated loopback/bridge HTTP reader, isolated from execution."""
import errno
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from socketserver import TCPServer
import threading
from urllib.parse import parse_qs, urlsplit

from .api_data import JournalView, query_options
from .security import REDACTOR, Redactor


PORT = 8795
HOSTS = ('127.0.0.1', '172.18.0.1')
ENDPOINTS = {f'/api/v1/{name}': name for name in ('summary', 'positions', 'trades', 'stats')}


def parse_bind(value):
    if value is None:
        return [(host, PORT) for host in HOSTS]
    result = []
    for item in value.split(','):
        host, sep, port = item.strip().partition(':')
        if host not in HOSTS or (sep and port != str(PORT)):
            raise ValueError('invalid_api_bind')
        address = (host, PORT)
        if address not in result:
            result.append(address)
    return result


def _log(logger, kind, **fields):
    if logger:
        try:
            logger(kind, **fields)
        except BaseException:
            pass  # Optional diagnostics must never propagate into execution.


class _Server(HTTPServer):
    # One bounded handler per listener; no unbounded client thread creation.
    allow_reuse_address = False
    request_queue_size = 8
    timeout = .2

    def server_bind(self):
        # HTTPServer.server_bind calls getfqdn (reverse DNS). Bind numeric IPs
        # directly so this read-only service never initiates a DNS/network lookup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(2)
        return sock, address

    def handle_error(self, request, client_address):
        self.service.log('api_request_failed')  # Never print exception/headers/path.


class _Handler(BaseHTTPRequestHandler):
    server_version = 'crossex-readonly'
    sys_version = ''

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # The stdlib HTML error page reflects untrusted paths and request lines.
        self.reply(code, {'error': 'invalid_request'})

    def reply(self, status, value):
        self.close_connection = True
        body = json.dumps(self.server.service.clean(value), ensure_ascii=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        if status == 405:
            self.send_header('Allow', 'GET')
        self.end_headers()
        if getattr(self, 'command', None) != 'HEAD':
            self.wfile.write(body)

    def __getattr__(self, name):
        # Even unknown HTTP verbs go through authentication and return 405.
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)

    def dispatch(self):
        service = self.server.service
        tokens = self.headers.get_all('X-Api-Token', [])
        token = tokens[0] if len(tokens) == 1 else ''
        authenticated = hmac.compare_digest(token.encode('utf-8'), service.token)
        if not authenticated:
            return self.reply(401, {'error': 'unauthorized'})
        if self.command != 'GET':
            return self.reply(405, {'error': 'method_not_allowed'})
        if len(self.path) > 2048 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            return self.reply(400, {'error': 'invalid_request'})
        try:
            url = urlsplit(self.path)
            if url.scheme or url.netloc or url.fragment:
                raise ValueError('invalid_request')
            endpoint = ENDPOINTS.get(url.path)
            if endpoint is None:
                return self.reply(404, {'error': 'not_found'})
            values = (parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
                      if url.query else {})
            if any(len(v) != 1 for v in values.values()):
                raise ValueError('invalid_request')
            query = {k: v[0] for k, v in values.items()}
            query_options(endpoint, query)
        except (ValueError, TypeError, OverflowError):
            return self.reply(400, {'error': 'invalid_query'})
        if not service.read_slot.acquire(blocking=False):
            return self.reply(503, {'error': 'temporarily_unavailable'})
        try:
            try:
                value = service.view.get(endpoint, query)
                # Serialize inside the exception boundary: nonfinite/corrupt DB rows
                # and redaction failures must never leak a traceback or a partial body.
                value = service.clean(value)
                json.dumps(value, allow_nan=False)
            except BaseException:
                service.log('api_read_failed')
                return self.reply(503, {'error': 'temporarily_unavailable'})
            return self.reply(200, value)
        finally:
            service.read_slot.release()


class APIService:
    def __init__(self, config, path, logger):
        self.token = config['CROSSEX_EXEC_API_TOKEN'].encode('utf-8')
        self.bind = parse_bind(config.get('CROSSEX_EXEC_API_BIND'))
        self.view = JournalView(path, config)
        self.logger = logger
        self.redactor = Redactor()
        self.redactor.register(*(v for k, v in config.items()
                                 if any(s in k for s in ('KEY', 'SECRET', 'PASSPHRASE', 'TOKEN'))))
        self.read_slot = threading.BoundedSemaphore(1)
        self.stopped, self.ready = threading.Event(), threading.Event()
        self.servers, self.addresses, self.threads = [], [], []
        self.lock = threading.Lock()

    def clean(self, value):
        # Projection fixes all output keys; also redact registered credential/signature
        # values if they ever occur in an otherwise public identifier.
        return self.redactor.clean(REDACTOR.clean(value))

    def log(self, kind, **fields):
        _log(self.logger, kind, **fields)

    def start(self):
        thread = threading.Thread(target=self._start, name='exec-api-start', daemon=True)
        self.threads.append(thread)
        thread.start()

    def _start(self):
        try:
            for address in self.bind:
                if self.stopped.is_set():
                    break
                try:
                    server = _Server(address, _Handler)
                    server.service = self
                except OSError as error:
                    unavailable = error.errno == errno.EADDRNOTAVAIL or getattr(error, 'winerror', None) == 10049
                    self.log('api_bind_unavailable' if unavailable else 'api_bind_failed', host=address[0], port=PORT)
                    continue
                except BaseException:
                    self.log('api_bind_failed', host=address[0], port=PORT)
                    continue
                with self.lock:
                    if self.stopped.is_set():
                        server.server_close()
                        break
                    self.servers.append(server)
                    self.addresses.append(server.server_address)
                    thread = threading.Thread(target=self._serve, args=(server,),
                                              name='exec-api-'+address[0], daemon=True)
                    self.threads.append(thread)
                    thread.start()
                self.log('api_listening', host=address[0], port=PORT)
        except BaseException:
            self.log('api_start_failed')
        finally:
            self.ready.set()

    def _serve(self, server):
        try:
            while not self.stopped.is_set():
                server.handle_request()
        except BaseException:
            if not self.stopped.is_set():
                self.log('api_thread_failed')
        finally:
            server.server_close()

    def close(self):
        # No join/shutdown waiting on API I/O in the trading coordinator.
        try:
            self.stopped.set()
            with self.lock:
                for server in self.servers:
                    try:
                        server.server_close()
                    except BaseException:
                        pass
        except BaseException:
            self.log('api_close_failed')


def start_api(config, path, *, logger=None):
    token = config.get('CROSSEX_EXEC_API_TOKEN', '')
    if not token.strip():
        _log(logger, 'api_disabled_no_token')
        return None
    service = None
    try:
        service = APIService(config, path, logger)
        service.start()
        return service
    except BaseException:
        if service is not None:
            service.close()
        _log(logger, 'api_start_failed')
        return None
