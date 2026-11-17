"""Block kinds beyond heading and prose (docs/kinds.md).

``kind`` holds the declaration and the registry, ``builtins`` the shipped
kinds, ``write`` the boundary where content becomes ops, ``lift`` the loop's
pass that re-derives kinds from code. Importing this package registers the
shipped kinds.
"""
from codoc.kinds import builtins
from codoc.kinds.kind import Kind, LiftContext, LowerContext, get, kinds, plain, register

builtins.register_all()

__all__ = ["Kind", "LiftContext", "LowerContext", "get", "kinds", "plain", "register"]
