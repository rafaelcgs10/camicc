{
  description = "dcp2icc — convert DNG camera profiles (.dcp) to darktable-ready ICC input profiles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs:
        let
        dcp2icc = pkgs.python3Packages.buildPythonApplication {
          pname = "dcp2icc";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.numpy ];
        };
        # dcp2icc-fetch-dcps (dcp2icc/fetch_dcps.py) with innoextract and TLS
        # trust supplied by nix (the scratch Docker image has neither). The
        # Adobe-copyrighted profiles are downloaded at the user's machine at
        # runtime and never redistributed.
        fetch-dcps = pkgs.writeShellScriptBin "dcp2icc-fetch-dcps" ''
          export PATH=${nixpkgs.lib.makeBinPath [ pkgs.innoextract ]}:$PATH
          export SSL_CERT_FILE=''${SSL_CERT_FILE:-${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt}
          exec ${dcp2icc}/bin/dcp2icc-fetch-dcps "$@"
        '';
        in {
        default = dcp2icc;
        inherit dcp2icc fetch-dcps;
      } // nixpkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
        # Everything the comparative test harness needs: darktable and
        # RawTherapee plus python with the harness dependencies and a
        # `dcp2icc-compare` wrapper around testing/compare.py. Used by
        # testing/Dockerfile; also handy locally: nix build .#testing-env
        testing-env = pkgs.buildEnv {
          name = "dcp2icc-testing-env";
          paths = [
            pkgs.darktable
            pkgs.rawtherapee
            pkgs.exiftool
            fetch-dcps
            (pkgs.python3.withPackages (ps: [ ps.numpy ps.pillow ]))
            (pkgs.writeShellScriptBin "dcp2icc-compare" ''
              exec python3 ${self}/testing/compare.py "$@"
            '')
            (pkgs.writeShellScriptBin "dcp2icc-suite" ''
              exec python3 ${self}/testing/suite.py "$@"
            '')
            (pkgs.writeShellScriptBin "dcp2icc-sweep" ''
              exec python3 ${self}/testing/sweep.py "$@"
            '')
            # a fontconfig setup so darktable does not warn in containers
            (pkgs.runCommand "fonts-conf" { } ''
              mkdir -p $out/etc/fonts
              cp ${pkgs.makeFontsConf { fontDirectories = [ pkgs.dejavu_fonts ]; }} \
                 $out/etc/fonts/fonts.conf
            '')
          ];
        };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (ps: [ ps.numpy ps.pillow ])) ];
        };
      });
    };
}
