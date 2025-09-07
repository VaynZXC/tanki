from __future__ import annotations

"""Registration package for creating new WoT accounts.

Submodules:
 - email_provider: Common interfaces and models for email inbox providers
 - firstmail_provider: Firstmail provider implementation (dev fallback inside)
 - utils: Helpers like password generation and email parsing
 - wg_registration: Workflow to register a new WG/WoT account via browser automation
"""

__all__ = [
    "email_provider",
    "firstmail_provider",
    "utils",
    "wg_registration",
]


