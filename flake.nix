{
  description = "upyt";

  inputs = {
    nixpkgs.url = "nixpkgs/nixos-24.11";

    utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      utils,
    }:
    utils.lib.eachSystem utils.lib.defaultSystems (
      system:
      let
        pkgs = nixpkgs.legacyPackages."${system}";
        python = pkgs.python3;
      in
      rec {
        packages.types-pyserial = python.pkgs.buildPythonPackage rec {
          pname = "types_pyserial";
          version = "3.5.0.20250326";
          pyproject = true;

          src = python.pkgs.fetchPypi {
            inherit pname version;
            hash = "sha256-u1ik7l9ZzPzNxp6c1HrksWPPjgsmCeDrNNCWkq9bsvE=";
          };

          nativeBuildInputs = [ python.pkgs.setuptools ];
        };

        packages.upyt = python.pkgs.buildPythonApplication rec {
          name = "upyt";
          pyproject = true;
          src = ./.;
          build-system = [ python.pkgs.setuptools ];
          dependencies = with python.pkgs; [
            pyserial
          ];
          nativeCheckInputs = with python.pkgs; [
            pytestCheckHook
            mypy
            websockets
            packages.types-pyserial
          ];
        };

        packages.default = packages.upyt;
      }
    );
}
