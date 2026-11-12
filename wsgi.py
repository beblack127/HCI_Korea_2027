"""Production WSGI entry point for the proposed system."""
from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app

app = create_app()
# Render terminates HTTPS at its proxy and forwards the original request scheme.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
