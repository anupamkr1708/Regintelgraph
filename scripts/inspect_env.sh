#!/usr/bin/env bash
# Read-only environment inspection for RegIntelGraph. Installs nothing, writes nothing.
set -u
have() { command -v "$1" >/dev/null 2>&1; }
ver()  { if have "$1"; then echo "$1: $("$@" 2>&1 | head -1)"; else echo "$1: not found"; fi; }

echo "== OS =="; uname -srm; grep -E '^(PRETTY_NAME)=' /etc/os-release 2>/dev/null || true
echo "== Resources =="; echo "cpus: $(nproc 2>/dev/null || echo ?)"; (free -h 2>/dev/null | sed -n 2p) || true; df -h . 2>/dev/null | tail -1
echo "== Python =="; ver python3 --version; ver python --version
echo "== Python package managers =="; ver pip3 --version; ver uv --version; ver poetry --version; ver pipx --version
echo "== Node =="; ver node --version; ver npm --version; ver pnpm --version; ver yarn --version
echo "== Containers =="; ver docker --version; ver podman --version
echo "== PostgreSQL =="; ver psql --version; ver pg_config --version; ver postgres --version
if have pg_isready; then pg_isready 2>&1 | head -1; else echo "pg_isready: not found"; fi
if have pg_config; then ls "$(pg_config --sharedir)/extension" 2>/dev/null | grep -i '^vector' | head -2 || true; fi
echo "== Local model runtimes / GPU =="; ver ollama --version; ver llama-cli --version; ver nvidia-smi --version
echo "== Git =="; ver git --version
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "inside git work tree: yes"; git status --short | head -20; git log --oneline -5 2>/dev/null
else echo "inside git work tree: no"; fi
echo "== Workspace =="; pwd; ls -la | head -30
