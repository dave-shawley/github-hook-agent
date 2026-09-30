# Keep AMQP publishing available without making startup broker-dependent

The webhook application uses a small `Publisher` with one AMQP connection and
channel, supervised in the background from the ASGI lifespan. It retries
connection failures with backoff, lets publishing wait for readiness only for a
bounded time, and reports failures through stable publisher exceptions. The
publisher does not declare RabbitMQ topology, buffer messages, use publisher
confirms, or replay an ambiguous write: topology remains externally managed and
the application must decide how to handle a failed publication.

This boundary avoids coupling application startup to RabbitMQ availability while
also avoiding the false delivery guarantees and operational complexity of an
in-memory queue or automatic replay. `pamqp` supplies AMQP frame types and
marshalling; the publisher owns only the connection lifecycle and publication
path it needs.
