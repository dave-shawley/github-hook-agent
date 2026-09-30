import os
import pathlib
import tempfile
import unittest

from github_webhook import rabbitmq


class PublisherUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._saved_env = {
            k: os.environ.pop(k)
            for k in os.environ
            if k.startswith('RABBITMQ_')
        }

    def tearDown(self) -> None:
        super().tearDown()
        os.environ.update(self._saved_env)

    def test_explicit_url_does_not_require_password_file(self) -> None:
        config = rabbitmq.RabbitConfiguration(
            url='amqp://user:password@broker/%2F',
            password_file=pathlib.Path('/does/not/exist'),
        )

        self.assertEqual(
            'amqp://user:password@broker/%2F', rabbitmq.publisher_url(config)
        )

    def test_url_uses_encoded_password_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            password_file = pathlib.Path(directory) / 'password'
            password_file.write_text('pa:ss@word/with-specials\n')
            config = rabbitmq.RabbitConfiguration(password_file=password_file)

            self.assertEqual(
                'amqp://github-webhook:pa%3Ass%40word%2Fwith-specials'
                '@rabbitmq:5672/%2F',
                rabbitmq.publisher_url(config),
            )

    def test_missing_password_file_is_reported_without_exposing_password(
        self,
    ) -> None:
        password_file = pathlib.Path('/does/not/exist')
        config = rabbitmq.RabbitConfiguration(password_file=password_file)

        with self.assertRaisesRegex(
            RuntimeError,
            r'^Unable to read RabbitMQ password file: /does/not/exist$',
        ):
            rabbitmq.publisher_url(config)

    def test_empty_password_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            password_file = pathlib.Path(directory) / 'password'
            password_file.write_text('\n')
            config = rabbitmq.RabbitConfiguration(password_file=password_file)

            with self.assertRaisesRegex(
                RuntimeError,
                r'^RabbitMQ password file is empty: .*/password$',
            ):
                rabbitmq.publisher_url(config)
