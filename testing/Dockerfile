# Comparative-testing image: darktable + RawTherapee + the camicc test
# harness, all pinned by flake.lock. Build from the repository root:
#
#   docker build -f testing/Dockerfile -t camicc-testing .
#
# Run with the same arguments as testing/compare.py, mounting a directory
# that contains the raw, the out-of-camera JPEG and the DCP:
#
#   docker run --rm -v "$PWD:/work" camicc-testing \
#       --raw IMG_9399.CR3 --jpeg IMG_9399.JPG \
#       --dcp "Canon EOS RP Camera Standard.dcp" -o results
#
# The image uses upstream darktable, so the tone mapper defaults to sigmoid.

FROM nixos/nix:2.35.1 AS build
COPY . /src
RUN nix --extra-experimental-features 'nix-command flakes' \
        build /src#testing-env -o /tmp/result \
 && mkdir -p /out/nix/store \
 && cp -a $(nix-store -qR /tmp/result) /out/nix/store/ \
 && ln -s $(readlink -f /tmp/result) /out/env

FROM alpine:3.20
COPY --from=build /out /
ENV PATH=/env/bin:$PATH \
    HOME=/work \
    CAMICC_TONEMAPPER=sigmoid \
    FONTCONFIG_FILE=/env/etc/fonts/fonts.conf
WORKDIR /work
ENTRYPOINT ["/env/bin/camicc-compare"]
