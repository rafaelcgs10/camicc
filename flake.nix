{
  description = "dcp2icc — convert DNG camera profiles (.dcp) to darktable-ready ICC input profiles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        default = dcp2icc;
        dcp2icc = pkgs.python3Packages.buildPythonApplication {
          pname = "dcp2icc";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.numpy ];
        };
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
