# GitHub hook messaging context

This context names the messaging concepts used by the webhook application and
its AMQP publisher.

## Messaging

**Publisher**:
An application-owned component that sends messages to RabbitMQ on behalf of a
webhook handler.
_Avoid_: Client, producer

**Publication**:
A message submission addressed to an exchange and routing key.
_Avoid_: Delivery, event

**Topology**:
The exchanges, queues, and bindings that determine how RabbitMQ routes a
publication; this is managed outside the application publisher.
_Avoid_: Setup, configuration
