#!/usr/bin/env python3
"""Robin-CTI: AI-Powered Dark Web OSINT & Workflow Viewer

Main entrypoint launching the Robin-CTI interactive web console.
"""

import argparse
import asyncio
import inspect
import logging
import os
import sys
from typing import List, Optional

from health import check_tor_proxy
from modules.viewer.server import run_viewer_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("robin")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for host and port bindings."""
    parser = argparse.ArgumentParser(
        description="Robin-CTI: AI-Powered Dark Web OSINT & Workflow Viewer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host interface to bind the web console to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ROBIN_PORT", os.getenv("PORT", 8090))),
        help="Port to listen on for HTTP & WebSocket traffic",
    )
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def init_cti_index() -> int:
    """Initialize or verify the CTI index database."""
    try:
        from modules.cti.indexer import get_db_connection

        conn = get_db_connection()
        cur = conn.execute("SELECT COUNT(*) FROM cti_records")
        count = cur.fetchone()[0]
        logger.info("CTI index loaded with %d threat intelligence records.", count)
        return count
    except Exception as exc:
        logger.warning("CTI index initialization skipped or failed: %s", exc)
        raise


def refresh_models() -> None:
    """Warm or refresh model provider registries."""
    try:
        import model_registry

        model_registry.refresh(verbose=False)
        logger.info("Model registry refreshed.")
    except Exception as exc:
        logger.debug("Model registry refresh skipped: %s", exc)


def main(argv: Optional[List[str]] = None) -> None:
    """Robin-CTI front-door entry point."""
    args = parse_args(argv)

    logger.info("Initializing Robin-CTI environment...")

    # Check Tor connectivity
    try:
        tor_status = check_tor_proxy()
        status = tor_status.get("status")
        if status == "up":
            logger.info("Tor proxy is connected (%sms latency).", tor_status.get("latency_ms"))
        elif status == "starting":
            logger.warning(
                "Tor proxy is still starting (%s). Searches may reach fewer engines.",
                tor_status.get("error"),
            )
        else:
            logger.warning(
                "Tor proxy is down (%s). Ensure Tor is running on 127.0.0.1:9050 for onion routing.",
                tor_status.get("error"),
            )
    except Exception as exc:
        logger.warning("Tor proxy check error: %s", exc)

    # Initialize CTI database
    try:
        init_cti_index()
    except Exception:
        pass

    # Warm model registry
    refresh_models()

    # Launch viewer server
    logger.info("Starting Robin-CTI Web Console on http://%s:%d ...", args.host, args.port)

    if inspect.iscoroutinefunction(run_viewer_server):
        return asyncio.run(run_viewer_server(host=args.host, port=args.port))
    else:
        res = run_viewer_server(host=args.host, port=args.port)
        if inspect.iscoroutine(res):
            return asyncio.run(res)
        return res


if __name__ == "__main__":
    main()
