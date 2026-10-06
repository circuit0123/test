"""Redis client. Redis only holds caches, so losing its data is always safe."""

from redis.asyncio import Redis


def create_redis(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)
