FROM alpine:3.21

RUN apk add --no-cache openssh-client autossh

ENTRYPOINT ["autossh", "-M", "0", "-N", \
    "-R", "0.0.0.0:1081", \
    "-o", "ServerAliveInterval=30", \
    "-o", "ServerAliveCountMax=3", \
    "-o", "ExitOnForwardFailure=yes", \
    "-o", "StrictHostKeyChecking=accept-new", \
    "-o", "UserKnownHostsFile=/root/.ssh/known_hosts", \
    "-i", "/root/.ssh/id_rsa"]
