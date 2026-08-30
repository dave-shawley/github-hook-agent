# Development environment notes

## RabbitMQ access

The passwords for the various rabbitmq users are stored as plaintext in the secrets subdirectory.

| User           | Notes                                    |
| -------------- | ---------------------------------------- |
| agent-consumer | Can only consume from appropriate queues |
| admin          | Full administrative access               |
| github-webhook | Can only publish messages to `webhooks`  |

You can use the `admin` user to log in on the management port to interact with the server.
