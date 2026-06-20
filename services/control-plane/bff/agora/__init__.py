"""Agora BFF sub-package — human-trader interaction plane.

Provides the create_agora_router() factory used by main.py to mount all Agora
surfaces under /bff/agora/. Actual handlers are filled by downstream AG-BE-ID-*
and AG-BE-SW-* tasks; this package defines the package boundary, envelope shape,
typed errors, and capability manifest.
"""
