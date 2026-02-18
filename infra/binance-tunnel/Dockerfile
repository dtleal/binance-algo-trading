FROM alpine:3.21

RUN apk add --no-cache openssh-client autossh build-base git && \
    git clone --depth 1 https://github.com/rofl0r/microsocks.git /tmp/microsocks && \
    cd /tmp/microsocks && make && cp microsocks /usr/local/bin/ && \
    rm -rf /tmp/microsocks && \
    apk del build-base git

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
