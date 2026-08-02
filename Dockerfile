# dcp2icc in a container. Built with Nix inside Docker so the image contains
# exactly the package versions pinned by flake.lock — no Nix needed on the
# host, only Docker:
#
#   docker build -t dcp2icc .
#   docker run --rm -v "$PWD:/work" dcp2icc "Canon EOS RP Camera Standard.dcp"
#
# The container's working directory is /work; mount the directory holding
# your .dcp files there and the .icc files are written next to them
# (or use -o as usual). See README.md for details.

FROM nixos/nix:2.35.1 AS build
COPY . /src
RUN nix --extra-experimental-features 'nix-command flakes' \
        build /src#dcp2icc -o /tmp/result \
 && nix --extra-experimental-features 'nix-command flakes' \
        build /src#fetch-dcps -o /tmp/fetch \
 && mkdir -p /out/nix/store \
 && cp -a $(nix-store -qR /tmp/result /tmp/fetch) /out/nix/store/ \
 && ln -s $(readlink -f /tmp/result) /out/app \
 && ln -s $(readlink -f /tmp/fetch) /out/fetch

FROM scratch
COPY --from=build /out /
ENV HOME=/work
WORKDIR /work
# dcp2icc-fetch-dcps is also available:
#   docker run --rm -v "$PWD:/work" --entrypoint /fetch/bin/dcp2icc-fetch-dcps dcp2icc
ENTRYPOINT ["/app/bin/dcp2icc"]
