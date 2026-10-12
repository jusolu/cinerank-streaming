import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app import app

class VercelMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        raw_uri = environ.get('RAW_URI', '')
        request_uri = environ.get('REQUEST_URI', '')
        path_info = environ.get('PATH_INFO', '')

        # When Vercel rewrites to /api/index, restore original client requested path
        if path_info == '/api/index' or path_info == '/api/index/':
            target = '/'
            if raw_uri:
                target = raw_uri.split('?')[0] or '/'
            elif request_uri:
                target = request_uri.split('?')[0] or '/'
            environ['PATH_INFO'] = target

        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelMiddleware(app.wsgi_app)
