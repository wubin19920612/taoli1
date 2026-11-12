"""Explicit signed transport, bounded deadlines, no redirects or implicit retries."""
import base64
import hashlib
import hmac
import http.client
import json
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlencode, urlsplit
from tools.fill_sim.network import DNS_RESOLVER, connect_addresses, DeadlineSocket
from .security import REDACTOR


def gate_sign(secret, method, path, query, body, timestamp):
    pre = '\n'.join((method.upper(), path, query, hashlib.sha512(body.encode()).hexdigest(), str(timestamp)))
    return hmac.new(secret.encode(), pre.encode(), hashlib.sha512).hexdigest()


def bitget_sign(secret, method, path, query, body, timestamp):
    pre = str(timestamp) + method.upper() + path + ('?' + query if query else '') + body
    return base64.b64encode(hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()).decode()


class APIError(Exception):
    def __init__(self, kind='unknown', *, http_status=None, api_code=None):
        # Never retain response body, request, headers, URL, or original exception.
        self.kind = kind if kind in {'unknown','not_found','duplicate','rejected','rate_limited',
                                    'shadow_write','credentials_missing','invalid_response','forbidden_endpoint'} else 'unknown'
        self.http_status=http_status if type(http_status) is int and 100<=http_status<=599 else None
        # Only published status codes, never arbitrary exchange messages.
        self.api_code=api_code if isinstance(api_code,str) and api_code in {
            '40009','40012','40014','40018','40036','40037','40040','40041','40042','40043',
            '40017','40762','40757','429','429000','TOO_MANY_REQUESTS',
            'INVALID_KEY','INVALID_SIGNATURE','IP_FORBIDDEN','FORBIDDEN','READ_ONLY',
            'INVALID_PARAM_VALUE','MISSING_REQUIRED_PARAM','INVALID_REQUEST_BODY'} else None
        super().__init__(self.kind)


class Transport:
    def __init__(self, venue, credentials, *, live=False, base=None, local_test=False, timeout=2):
        self.venue, self.credentials, self.live = venue, credentials, live
        expected = 'https://api.gateio.ws' if venue == 'gate' else 'https://api.bitget.com'
        self.base = base or expected
        parsed = urlsplit(self.base)
        if self.base != expected and not (local_test and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                                         and not parsed.path and not parsed.username and not parsed.query):
            raise ValueError('private_origin_not_allowed')
        self.parsed, self.timeout = parsed, timeout
        self.lock, self.history, self.paused = threading.Lock(), defaultdict(deque), 0
        self.inflight=defaultdict(int)

    def admit(self, group='query', budget_deadline=None):
        limit = (20 if self.venue == 'gate' else 10) if group == 'query' else (10 if group == 'write' else 5)
        if self.venue == 'bitget' and group == 'fee':
            limit = 3
        deadline = min(time.monotonic()+2,budget_deadline) if budget_deadline is not None else time.monotonic()+2
        while True:
            with self.lock:
                now = time.monotonic()
                history = self.history[group]
                while history and history[0] <= now-1.05:
                    history.popleft()
                if now<deadline and now >= self.paused and len(history)+self.inflight[group] < limit:
                    self.inflight[group]+=1
                    return
                if now >= deadline or self.paused > deadline:
                    raise APIError('rate_limited')
            time.sleep(.01)

    def request(self, method, path, params=None, body=None, before_send=None, group='query', deadline=None):
        if self.venue=='bitget' and not path.startswith('/api/v3/'):
            raise APIError('forbidden_endpoint')
        if method != 'GET' and not self.live:
            raise APIError('shadow_write')
        if not self.credentials.key or not self.credentials.secret or (self.venue == 'bitget' and not self.credentials.passphrase):
            raise APIError('credentials_missing')
        deadline=min(time.monotonic()+self.timeout,deadline) if deadline is not None else time.monotonic()+self.timeout
        self.admit(group,deadline)
        payload = json.dumps(body, separators=(',',':'), ensure_ascii=True) if body is not None else ''
        query = urlencode(sorted((params or {}).items()))
        parsed = self.parsed
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=self.timeout)
        response = None
        error = None
        status,code=None,None
        try:
            addresses = DNS_RESOLVER.resolve(parsed.hostname, deadline)
            conn._create_connection = lambda address, *args: connect_addresses(addresses, address[1], deadline, self.timeout, time.monotonic)
            conn.connect()
            if before_send:
                before_send()  # Admission and connection precede durable SENT.
                # The guard does bounded read-only requests. Give the write its
                # own bounded wire budget and sign AFTER the guard completes.
                deadline=time.monotonic()+self.timeout
            ts = str(int(time.time() * (1 if self.venue == 'gate' else 1000)))
            signature = (gate_sign if self.venue == 'gate' else bitget_sign)(self.credentials.secret,method,path,query,payload,ts)
            REDACTOR.signature(signature)
            headers = {'Content-Type':'application/json', 'Connection':'close', 'User-Agent':'taoli1-crossex-exec/1'}
            headers.update({'KEY':self.credentials.key, 'Timestamp':ts, 'SIGN':signature} if self.venue == 'gate' else
                           {'ACCESS-KEY':self.credentials.key, 'ACCESS-TIMESTAMP':ts, 'ACCESS-SIGN':signature,
                            'ACCESS-PASSPHRASE':self.credentials.passphrase})
            conn.sock.settimeout(max(.001,deadline-time.monotonic()))
            on_send=getattr(before_send,'on_send',None)
            if on_send:
                on_send()  # In-memory observation only; never a per-leg risk gate.
            conn.request(method, path+('?' + query if query else ''), body=payload.encode(), headers=headers)
            response = http.client.HTTPResponse(DeadlineSocket(conn.sock,deadline,self.timeout,time.monotonic), method=method)
            response.begin()
            status=response.status
            raw = response.read(2*1024*1024+1)
            if len(raw) > 2*1024*1024:
                raise APIError('invalid_response')
            data = json.loads(raw)
            code = str(data.get('label' if self.venue=='gate' else 'code','')) if isinstance(data,dict) else ''
            if response.status == 429 or code in ('429','429000','TOO_MANY_REQUESTS'):
                with self.lock:
                    self.paused = time.monotonic()+60
                raise APIError('rate_limited')
            if response.status >= 500 or 300 <= response.status < 400:
                raise APIError('unknown')
            if response.status >= 400 or (self.venue=='bitget' and code!='00000') or (self.venue=='gate' and code):
                if code in ('TRADE_ORDER_NOT_FOUND_ERROR','ORDER_NOT_FOUND','43001','45057'):
                    raise APIError('not_found')
                if code in ('TRADE_ORDER_DUPLICATE_ERROR','45034'):
                    raise APIError('duplicate')
                if code in ('TRADE_INVALID_ORDER_QTY','TRADE_INVALID_LIMIT_PRICE','TRADE_INSUFFICIENT_AVAILABLE_ERROR',
                            '40017','40762','40757'):
                    raise APIError('rejected')
                raise APIError('unknown')
            return data if self.venue=='gate' else data['data']
        except APIError as exc:
            error = exc.kind
        except Exception:
            error = 'unknown'
        finally:
            if response:
                response.close()
            conn.close()
            with self.lock:
                self.inflight[group]-=1
                self.history[group].append(time.monotonic())
        # Raise outside except: no secret-bearing exception __context__ chain.
        raise APIError(error,http_status=status,api_code=code)
