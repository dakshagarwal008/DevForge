"""GitHub repository, issue and library discovery (Person 3 module)."""
from .models import DiscoveryQuery, DiscoveryResponse
from .service import DiscoveryService, discover

__all__ = ["discover", "DiscoveryService", "DiscoveryQuery", "DiscoveryResponse"]
