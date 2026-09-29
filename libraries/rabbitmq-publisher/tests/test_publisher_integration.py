import os
import unittest
import urllib.parse
import uuid

import dotenv
import httpx2
import pytest
from rabbitmq_publisher import api

dotenv.load_dotenv()


def _required_environment(*names: str) -> dict[str, str]:
    values = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        pytest.skip(f'missing integration environment: {", ".join(missing)}')
    return {name: value for name, value in values.items() if value is not None}


pytestmark = pytest.mark.integration


class PublisherIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_publisher_publishes_to_a_temporary_rabbitmq_queue(
        self,
    ) -> None:
        env = _required_environment(
            'RABBITMQ_ADMIN_PASSWORD',
            'RABBITMQ_ADMIN_USER',
            'RABBITMQ_HOST',
            'RABBITMQ_PORT_15672',
            'RABBITMQ_PORT_5672',
            'RABBITMQ_PUBLISH_PASSWORD',
            'RABBITMQ_PUBLISH_USER',
        )
        vhost = '/'
        queue = str(uuid.uuid4())
        routing_key = queue
        message = f'publisher integration test {uuid.uuid4()}'
        encoded_vhost = urllib.parse.quote(vhost, safe='')
        encoded_queue = urllib.parse.quote(queue, safe='')
        management_url = (
            f'http://{env["RABBITMQ_HOST"]}:{env["RABBITMQ_PORT_15672"]}/api'
        )
        publish_user = urllib.parse.quote(
            env['RABBITMQ_PUBLISH_USER'], safe=''
        )
        publish_password = urllib.parse.quote(
            env['RABBITMQ_PUBLISH_PASSWORD'], safe=''
        )
        publisher_url = (
            f'amqp://{publish_user}:{publish_password}@'
            f'{env["RABBITMQ_HOST"]}:{env["RABBITMQ_PORT_5672"]}/%2F'
            '?connection_timeout=3'
        )

        async with httpx2.AsyncClient(
            auth=(env['RABBITMQ_ADMIN_USER'], env['RABBITMQ_ADMIN_PASSWORD']),
            base_url=management_url,
            timeout=5,
        ) as management:
            try:
                response = await management.put(
                    f'/queues/{encoded_vhost}/{encoded_queue}',
                    json={
                        'durable': True,
                        'auto_delete': False,
                        'arguments': {'x-expires': 60_000},
                    },
                )
                response.raise_for_status()

                response = await management.post(
                    f'/bindings/{encoded_vhost}/e/webhooks/q/{encoded_queue}',
                    json={'routing_key': routing_key, 'arguments': {}},
                )
                response.raise_for_status()

                async with api.Publisher(publisher_url) as publisher:
                    await publisher.publish(
                        exchange='webhooks',
                        routing_key=routing_key,
                        message_body=message,
                        content_type='text/plain',
                    )

                response = await management.post(
                    f'/queues/{encoded_vhost}/{encoded_queue}/get',
                    json={
                        'count': 1,
                        'ackmode': 'ack_requeue_false',
                        'encoding': 'auto',
                        'truncate': 50_000,
                    },
                )
                response.raise_for_status()
                messages = response.json()
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0]['payload'], message)
                self.assertEqual(messages[0]['exchange'], 'webhooks')
                self.assertEqual(messages[0]['routing_key'], routing_key)
            finally:
                response = await management.delete(
                    f'/queues/{encoded_vhost}/{encoded_queue}'
                )
                self.assertIn(response.status_code, (204, 404))
