{
  description = "camicc — convert DNG camera profiles (.dcp) to darktable-ready ICC input profiles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs:
        let
        camicc = pkgs.python3Packages.buildPythonApplication {
          pname = "camicc";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.numpy ];
        };
        # camicc-fetch-dcps (camicc/fetch_dcps.py) with innoextract and TLS
        # trust supplied by nix (the scratch Docker image has neither). The
        # Adobe-copyrighted profiles are downloaded at the user's machine at
        # runtime and never redistributed.
        fetch-dcps = pkgs.writeShellScriptBin "camicc-fetch-dcps" ''
          export PATH=${nixpkgs.lib.makeBinPath [ pkgs.innoextract ]}:$PATH
          export SSL_CERT_FILE=''${SSL_CERT_FILE:-${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt}
          exec ${camicc}/bin/camicc-fetch-dcps "$@"
        '';
        # camicc-styles (camicc/styles.py): generate darktable styles for the
        # headroom variant. Pure python, so just a thin wrapper on the bin.
        styles = pkgs.writeShellScriptBin "camicc-styles" ''
          exec ${camicc}/bin/camicc-styles "$@"
        '';
        in {
        default = camicc;
        inherit camicc fetch-dcps styles;
        dcp2icc = camicc;    # deprecated alias
      } // nixpkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
        # Everything the comparative test harness needs: darktable and
        # RawTherapee plus python with the harness dependencies and a
        # `camicc-compare` wrapper around testing/compare.py. Used by
        # testing/Dockerfile; also handy locally: nix build .#testing-env
        testing-env = pkgs.buildEnv {
          name = "camicc-testing-env";
          paths = [
            pkgs.darktable
            pkgs.rawtherapee
            pkgs.exiftool
            fetch-dcps
            (pkgs.python3.withPackages (ps: [ ps.numpy ps.pillow ]))
            (pkgs.writeShellScriptBin "camicc-compare" ''
              exec python3 ${self}/testing/compare.py "$@"
            '')
            (pkgs.writeShellScriptBin "camicc-suite" ''
              exec python3 ${self}/testing/suite.py "$@"
            '')
            (pkgs.writeShellScriptBin "camicc-sweep" ''
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
