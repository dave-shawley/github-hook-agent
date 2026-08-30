export COMPOSE_FILE := "../dev/compose.yaml"

[doc("Bring up development services")]
up:
    docker compose up --detach --wait --wait-timeout 120

[doc("Shutdown development services")]
down:
    docker compose down --remove-orphans --volumes

[doc("List services and port mappings, use jq .{service}.port_{port} to extract an ephemeral port for a service")]
ports:
    #!/usr/bin/env sh
    docker compose ps --format json | jq -s '
      [
        .[]|{
          "key":(.Name|ltrimstr("dev-")|rtrimstr("-1")),
          "value": [
            .Publishers[]
            |select(.PublishedPort!=0)
            |{"key":"port_\(.TargetPort)", "value": .PublishedPort}
          ]|from_entries
      }]|from_entries
    '
