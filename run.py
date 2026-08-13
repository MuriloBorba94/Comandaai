from __future__ import annotations

import os

from waitress import serve

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = app.config.get("HOST", "0.0.0.0")
    port = int(app.config.get("PORT", 5000))
    print(f"Comanda ai ativo em http://{host}:{port}")
    serve(app, host=host, port=port)
