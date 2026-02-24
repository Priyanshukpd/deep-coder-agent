"""
Plugin Loader — Dynamic loading for the Federated Plugin Hub.
Part of Phase 70.
"""

import os
import json
import importlib.util
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

MARKETPLACE_FILE = "plugins/marketplace.json"

class PluginLoader:
    """
    Handles dynamic discovery and instantiation of plugins from the marketplace.
    """

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.marketplace_path = os.path.join(repo_path, MARKETPLACE_FILE)
        self.plugins_metadata = self._load_metadata()

    def _load_metadata(self) -> list:
        if os.path.exists(self.marketplace_path):
            try:
                with open(self.marketplace_path, 'r', encoding='utf-8') as f:
                    return json.load(f).get("plugins", [])
            except Exception as e:
                logger.error(f"Failed to load marketplace metadata: {e}")
        return []

    def load_plugin(self, plugin_id: str) -> Any:
        """Dynamically import and instantiate a plugin by ID."""
        meta = next((p for p in self.plugins_metadata if p["id"] == plugin_id), None)
        if not meta:
            logger.error(f"Plugin {plugin_id} not found in marketplace.")
            return None

        plugin_path = os.path.join(self.repo_path, meta["path"])
        class_name = meta["class"]

        if not os.path.exists(plugin_path):
            logger.error(f"Plugin file not found at {plugin_path}")
            return None

        try:
            # Dynamic import logic
            spec = importlib.util.spec_from_file_location(f"plugin_{plugin_id}", plugin_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            plugin_class = getattr(module, class_name)
            logger.info(f"Successfully loaded plugin: {meta['name']}")
            return plugin_class
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_id}: {e}")
            return None

    def get_all_plugins(self) -> Dict[str, Any]:
        """Load all registered plugins."""
        instances = {}
        for meta in self.plugins_metadata:
            klass = self.load_plugin(meta["id"])
            if klass:
                try:
                    instances[meta["id"]] = klass()
                except Exception as e:
                    logger.error(f"Failed to instantiate {meta['id']}: {e}")
        return instances
