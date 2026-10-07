"""Start EmailManager: python run.py  →  http://127.0.0.1:5055 (production server, this computer only)"""
import logging
import os
import sys

from waitress import serve

from app import server, worker

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    port = int(os.environ.get("EMAILMANAGER_PORT", "5055"))
    app = server.create_app()
    worker.start()
    print(f"EmailManager running at http://127.0.0.1:{port}", file=sys.stderr, flush=True)
    serve(app, host="127.0.0.1", port=port, threads=8, ident="EmailManager")
