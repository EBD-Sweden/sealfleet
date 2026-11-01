# SQL assets for the Helm migrate Job (migrate.sqlImage). Build from the repo
# root so the image carries the same migrations as the service images:
#   docker build -f db/Dockerfile -t sealfleet-migrate-sql:<tag> .
FROM alpine:3.20
COPY db/ /sql/db/
COPY scripts/001_create_api_keys.sql /sql/scripts/
