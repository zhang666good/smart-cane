from .audio import router as audio_router
from .device import router as device_router
from .auth import router as auth_router
from .family import router as family_router
from .admin import router as admin_router
from .voice import router as voice_router
from .ai import router as ai_router

__all__ = ["audio_router", "device_router", "auth_router", "family_router",
           "admin_router", "voice_router", "ai_router"]