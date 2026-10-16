"""Local fixture HTTP server for readlater tests: http.server over ROOT, plus
/code/NNN (that status), /slow/<path> (3 s delay first), /huge (claims 50 MB), /redirect?to=URL (302),
/gzip/<path> (gzip-encoded), /octet/<path> (application/octet-stream), and /robots.txt answering the status in
ROOT/.robots_status if that file exists. Logs "path status UA=..." per request to LOGFILE; port to PORTFILE."""
import functools, gzip, http.server, os, sys, time, urllib.parse

ROOT, PORTFILE, LOGFILE = sys.argv[1], sys.argv[2], sys.argv[3]


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        code = a[1] if len(a) > 1 else '?'
        with open(LOGFILE, 'a') as f:
            f.write(f"{self.path.split('?')[0]} {code} UA={self.headers.get('User-Agent')}\n")

    def raw(self, code, body=b'', ctype='text/plain', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path
        if path == '/robots.txt' and os.path.exists(os.path.join(ROOT, '.robots_status')):
            return self.raw(int(open(os.path.join(ROOT, '.robots_status')).read().strip()))
        if path.startswith('/code/'):
            return self.raw(int(path.split('/')[2]), b'nope')
        if path == '/huge':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', '50000000')
            self.end_headers()
            self.wfile.write(b'<html>')
            return
        if path == '/redirect':
            to = urllib.parse.parse_qs(u.query)['to'][0]
            return self.raw(302, b'', extra={'Location': to})
        if path.startswith('/gzip/'):
            data = open(os.path.join(ROOT, path[len('/gzip/'):]), 'rb').read()
            return self.raw(200, gzip.compress(data), 'text/html; charset=utf-8', {'Content-Encoding': 'gzip'})
        if path.startswith('/octet/'):
            data = open(os.path.join(ROOT, path[len('/octet/'):]), 'rb').read()
            return self.raw(200, data, 'application/octet-stream')
        if path.startswith('/slow/'):
            time.sleep(3)
            self.path = self.path[len('/slow'):]
        return super().do_GET()


srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(H, directory=ROOT))
with open(PORTFILE + '.tmp', 'w') as f:
    f.write(str(srv.server_address[1]))
os.replace(PORTFILE + '.tmp', PORTFILE)
srv.serve_forever()
