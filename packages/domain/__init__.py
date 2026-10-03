"""Pure domain types, enums and invariants (AGENTS.md layering: imports no other project package, performs no I/O).

Types here are frozen stdlib dataclasses/enums. Pydantic is an eventual *boundary* technology only and never appears in
this package. Which standard-library modules may be imported here is enforced by `scripts/check_imports.py`.
"""
