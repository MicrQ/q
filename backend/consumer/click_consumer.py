"""
Standalone Click Consumer.

Reads click events from the Redis Stream 'clicks' on redis_1,
batches the click increments, writes them to Postgres, and acknowledges the messages.
Uses consumer groups for durability.
"""

import asyncio
import logging
import signal
from collections import Counter
from datetime import datetime, timezone

import redis.asyncio as aioredis
from asyncpg import InterfaceError

from app import db, cache, config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("click_consumer")

CONSUMER_GROUP = "click_workers"
CONSUMER_NAME = "consumer_1"
BATCH_SIZE = 100
BLOCK_MS = 2000

# Control flag for loop termination
running = True


def handle_exit_signal(sig, frame):
    global running
    logger.info("Received shutdown signal. Stopping consumer...")
    running = False


async def ensure_consumer_group(redis_client: aioredis.Redis):
    """Ensure the clicks stream and consumer group exist."""
    try:
        # Create stream and group if they don't exist
        # '$' means we only consumer new messages added from now on
        await redis_client.xgroup_create(
            name=cache.CLICKS_STREAM,
            groupname=CONSUMER_GROUP,
            id="$",
            mkstream=True
        )
        logger.info(f"Created consumer group {CONSUMER_GROUP} on stream {cache.CLICKS_STREAM}")
    except aioredis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group {CONSUMER_GROUP} already exists.")
        else:
            raise e


async def process_batch(events: list, redis_client: aioredis.Redis) -> bool:
    """
    Process a batch of events from XREADGROUP.
    Groups counts by short code and performs atomic increments in the database.
    Returns True on success, False if database write failed.
    """
    if not events:
        return True

    # Parse message data and message IDs
    # Structure from redis-py xreadgroup:
    # [ (stream_name, [ (message_id, {key: val}), ... ]) ]
    msg_ids = []
    click_counts = Counter()

    for stream, messages in events:
        for msg_id, data in messages:
            code = data.get("code")
            if code:
                click_counts[code] += 1
            msg_ids.append(msg_id)

    if not click_counts:
        # Nothing valid to process, ack immediately
        if msg_ids:
            await redis_client.xack(cache.CLICKS_STREAM, CONSUMER_GROUP, *msg_ids)
        return True

    # Persist increments to Postgres
    try:
        # Perform updates sequentially (can also run in parallel but simple is safer)
        for code, count in click_counts.items():
            await db.increment_clicks(code, count)
        
        # Acknowledge processed messages
        await redis_client.xack(cache.CLICKS_STREAM, CONSUMER_GROUP, *msg_ids)
        logger.info(f"Successfully processed and acked {len(msg_ids)} click event(s)")
        return True
    except Exception as e:
        logger.error(f"Error persisting clicks to database: {e}")
        return False


async def main():
    global running
    logger.info("Starting click consumer service...")

    # Hook exit signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: handle_exit_signal(sig, None))
        except NotImplementedError:
            # Signal handling fallback for systems where loop.add_signal_handler is missing (e.g. Windows, though target is Linux)
            signal.signal(sig, handle_exit_signal)

    # Initialize Postgres Connection Pool
    await db.init_pool()

    # Get Redis clients. Clicks stream always lives on redis_1.
    redis_clients = cache.get_clients()
    first_node = config.REDIS_NODES[0]
    redis_client = redis_clients[first_node]

    # Ensure stream & group are present
    await ensure_consumer_group(redis_client)

    logger.info("Consumer loop started.")
    while running:
        try:
            # Read messages from group
            # '>' means only new messages that haven't been delivered to other consumers
            events = await redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={cache.CLICKS_STREAM: ">"},
                count=BATCH_SIZE,
                block=BLOCK_MS
            )

            if events:
                success = await process_batch(events, redis_client)
                if not success:
                    # DB error occurred, sleep a bit before retrying to prevent hot loops
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in consumer loop: {e}")
            await asyncio.sleep(2)

    # Graceful Shutdown
    logger.info("Cleaning up connections...")
    await db.close_pool()
    await cache.close_clients()
    logger.info("Consumer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
