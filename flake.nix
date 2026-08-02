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
        # Download Adobe DNG Converter from adobe.com and extract its camera
        # profiles (no installation, no Wine: the installer is Inno Setup and
        # innoextract unpacks it directly). Runs at the user's machine at
        # runtime — the Adobe-copyrighted profiles are never redistributed.
        fetch-dcps = pkgs.writeShellScriptBin "dcp2icc-fetch-dcps" ''
          set -eu
          export PATH=${nixpkgs.lib.makeBinPath [
            pkgs.curl pkgs.innoextract pkgs.coreutils pkgs.findutils
          ]}:$PATH
          # self-contained TLS trust (the scratch Docker image has none)
          export SSL_CERT_FILE=''${SSL_CERT_FILE:-${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt}
          out=dcps
          installer=""
          while [ $# -gt 0 ]; do
            case "$1" in
              -o) out=$2; shift 2 ;;
              -h|--help)
                echo "usage: dcp2icc-fetch-dcps [-o OUTDIR] [INSTALLER.exe]"
                echo "Downloads Adobe DNG Converter (about 1.8 GB) and extracts"
                echo "its DCP camera profiles into OUTDIR (default: ./dcps)."
                echo "Pass an already-downloaded installer to skip the download."
                exit 0 ;;
              *) installer=$1; shift ;;
            esac
          done
          # temp dir on the working volume: the download is ~1.8 GB and
          # minimal containers may lack a usable /tmp
          tmp=$(mktemp -d -p "$PWD" .dcp2icc-fetch.XXXXXX)
          trap 'rm -rf "$tmp"' EXIT
          if [ -z "$installer" ]; then
            echo "downloading Adobe DNG Converter (about 1.8 GB) from adobe.com ..."
            curl -L --progress-bar -o "$tmp/dng.exe" \
              https://www.adobe.com/go/dng_converter_win
            installer=$tmp/dng.exe
          fi
          echo "extracting camera profiles (nothing is installed or executed) ..."
          innoextract -s -I commonappdata/Adobe/CameraRaw/CameraProfiles \
            -d "$tmp/x" "$installer"
          mkdir -p "$out"
          cp -r "$tmp/x/commonappdata/Adobe/CameraRaw/CameraProfiles/." "$out/"
          n=$(find "$out" -name '*.dcp' | wc -l)
          echo "$n DCP profiles in $out/ (Camera/<model>/ and Adobe Standard/)"
          echo "NOTE: the profiles are copyrighted by Adobe - for your own" \
               "use only, do not commit or redistribute them."
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
