# Exit Ticket web image: nginx serving the student and teacher pages, and proxying /api/.
# Build for GKE's CPU architecture (from the repo root):
#   docker buildx build --platform linux/amd64 -t exit-ticket-web:dev --load app/web
# Run it with API_HOST=<host>:<port> of the API; there is deliberately no default.

# Unprivileged variant: runs as UID 101 and listens on 8080 instead of 80.
# Pinned to an exact nginx release.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

# Have the entrypoint set NGINX_LOCAL_RESOLVERS (the container's DNS servers, from
# /etc/resolv.conf), which nginx.conf uses to keep re-checking API_HOST's IP.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

# Remove the sample pages, so the web root holds exactly app/web/public.
# (The base image runs as nginx; switch to root just for this, then back.)
USER root
RUN rm -rf /usr/share/nginx/html/*
USER 101

# The nginx config is a template, outside the web root, so it can never be served.
# At startup the entrypoint fills in ${API_HOST} and writes /etc/nginx/conf.d/default.conf.
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY public /usr/share/nginx/html

# Startup check: refuse to start without API_HOST (see the script).
COPY --chmod=755 docker-entrypoint.d/05-require-api-host.sh /docker-entrypoint.d/

EXPOSE 8080

# CMD and STOPSIGNAL come from the base image: nginx in the foreground, stopped with SIGQUIT.
