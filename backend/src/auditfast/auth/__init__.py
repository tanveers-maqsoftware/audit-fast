"""Delegated, read-only authentication to Microsoft Fabric."""

from auditfast.auth.device_code import AuthError, DeviceCodeAuthenticator, get_authenticator

__all__ = ["AuthError", "DeviceCodeAuthenticator", "get_authenticator"]
