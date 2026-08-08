FROM eceasy/cli-proxy-api:v7.2.120

COPY entrypoint.sh /usr/local/bin/flow2api-cliproxy-entrypoint
RUN chmod 0755 /usr/local/bin/flow2api-cliproxy-entrypoint

EXPOSE 8317

ENTRYPOINT ["/usr/local/bin/flow2api-cliproxy-entrypoint"]

