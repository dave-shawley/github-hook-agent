export COMPOSE_FILE := justfile_directory() / "/dev/compose.yaml"
SECRETS_DIR := justfile_directory() / "dev/secrets"

[doc("Bring up development services")]
[no-cd]
up *SERVICES:
    docker compose up --detach --wait --wait-timeout 120 {{ SERVICES }}
    touch .env
    chmod 0600 .env
    just docker::env-file >> .env

[doc("Shutdown development services")]
[no-cd]
down *SERVICES:
    docker compose down --remove-orphans --volumes {{ SERVICES }}
    rm -f .env

[doc("List services and port mappings, use jq .{service}.port_{port} to extract an ephemeral port for a service")]
[no-cd]
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

[doc("Generate a .env file of published ephemeral ports")]
[no-cd]
env-file:
    #!/usr/bin/env sh
    docker compose ps --format json | jq -sr '
      [
        .[]
        |select(any(.Publishers[];(.PublishedPort!=0)))
        |(.Service|gsub("-";"_")|ascii_upcase) as $service
        |.Publishers[]
        |select(.PublishedPort!=0)|
        "\($service)_PORT_\(.TargetPort)=\(.PublishedPort)"
      ]
      |unique|.[]
    '
    echo 'RABBITMQ_HOST=127.0.0.1'

    user_env() {
      echo "RABBITMQ_$1_USER=$2"
      password="$(cat {{ SECRETS_DIR }}/rabbitmq-$2-password.txt)"
      echo "RABBITMQ_$1_PASSWORD=$password"
    }

    user_env ADMIN admin
    user_env CONSUMER agent-consumer
    user_env PUBLISH github-webhook
