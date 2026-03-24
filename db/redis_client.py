"""Redis client singleton."""

import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv(".env.local")
except Exception:
    pass

try:
    import redis
except Exception:
    redis = None

logger = logging.getLogger(__name__)
_client = None


def get_redis():
    global _client
    if _client is not None:
        return _client
    url = os.getenv("REDIS_URL", "")
    if not url or redis is None:
        return None
    try:
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
            retry_on_timeout=False,
        )
        _client.ping()
        logger.info("Redis client initialized")
        return _client
    except Exception:
        _client = None
        return None
