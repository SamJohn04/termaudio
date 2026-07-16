{
  description = "termaudio flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs = { self, nixpkgs }: let
    system = "x86_64-linux";
    pkgs = import nixpkgs { inherit system; };
    python = pkgs.python314.withPackages (ps: with ps; [
      pydub
      pyaudio
      audioop-lts
    ]);
  in {
    devShells.${system}.default = pkgs.mkShell {
      packages = [ python ];
    };
  };
}
