"""Sentence file management for Alarms and Reminders integration."""
import logging
from pathlib import Path
from typing import Any

import aiofiles
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LANGUAGES = ["en", "de", "fr"]
SENTENCE_TYPES = ["alarms", "reminders"]
SENTENCE_FILES_KEY = f"{DOMAIN}_sentence_files"
SENTENCE_SETUP_KEY = f"{DOMAIN}_sentence_setup"


async def async_setup_sentence_files(hass: HomeAssistant) -> None:
    """Copy and convert sentence files from integration to config directory.

    This function:
    1. Checks if sentence files have already been set up (to avoid duplicates)
    2. Loads sentence definitions from Python modules
    3. Converts them to YAML format
    4. Writes them to config/custom_sentences/<language>/ directory
    5. Tracks created files for cleanup
    """
    # Prevent duplicate setup if already done
    if hass.data.get(SENTENCE_SETUP_KEY, False):
        _LOGGER.debug("Sentence files already set up, skipping")
        return

    # Get paths
    integration_root = Path(__file__).parent
    source_dir = integration_root / "sentences"
    config_dir = integration_root.parent.parent  # Go up to config/
    target_base = config_dir / "custom_sentences"

    # Verify source directory exists (use executor to avoid blocking)
    if not await hass.async_add_executor_job(source_dir.exists):
        _LOGGER.error(
            "Sentence source directory not found: %s. Integration may be corrupted.",
            source_dir,
        )
        return

    _LOGGER.info("Setting up sentence files from %s to %s", source_dir, target_base)

    # Track created files for cleanup and whether any files changed
    created_files = []
    files_changed = False

    # Process each language and sentence type
    for language in LANGUAGES:
        for sentence_type in SENTENCE_TYPES:
            try:
                # Import the sentence module
                module_name = f".sentences.{language}.{sentence_type}"
                module = __import__(
                    f"custom_components.alarms_and_reminders.sentences.{language}.{sentence_type}",
                    fromlist=["DEFAULT_SENTENCES"],
                )

                # Extract sentence data
                if not hasattr(module, "DEFAULT_SENTENCES"):
                    _LOGGER.warning(
                        "Module %s missing DEFAULT_SENTENCES, skipping", module_name
                    )
                    continue

                sentence_data = module.DEFAULT_SENTENCES

                # Create target directory (use executor to avoid blocking)
                target_dir = target_base / language
                await hass.async_add_executor_job(
                    lambda: target_dir.mkdir(parents=True, exist_ok=True)
                )

                # Write YAML file
                target_file = target_dir / f"alarms&reminders_{sentence_type}.yaml"

                # Convert to YAML string first
                yaml_content = yaml.safe_dump(
                    sentence_data,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )

                # Check if file exists and content changed
                file_exists = await hass.async_add_executor_job(target_file.exists)
                content_changed = True

                if file_exists:
                    # Read existing content to compare
                    async with aiofiles.open(target_file, "r", encoding="utf-8") as f:
                        existing_content = await f.read()
                    content_changed = existing_content != yaml_content

                # Write async to avoid blocking
                async with aiofiles.open(target_file, "w", encoding="utf-8") as f:
                    await f.write(yaml_content)

                created_files.append(str(target_file))

                if content_changed:
                    files_changed = True
                    _LOGGER.debug("Created/updated sentence file: %s", target_file)
                else:
                    _LOGGER.debug("Sentence file unchanged: %s", target_file)

            except ImportError as err:
                _LOGGER.warning(
                    "Failed to import sentence module %s.%s: %s",
                    language,
                    sentence_type,
                    err,
                )
            except Exception as err:
                _LOGGER.error(
                    "Failed to process sentence file %s.%s: %s",
                    language,
                    sentence_type,
                    err,
                    exc_info=True,
                )

    # Store created files for cleanup and mark as set up
    hass.data[SENTENCE_FILES_KEY] = created_files
    hass.data[SENTENCE_SETUP_KEY] = True

    # Create a repair issue if files were created or modified
    if files_changed:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "sentence_files_updated",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="sentence_files_restart_required_to_take_effect. Make sure to add the [intent_script](https://github.com/omaramin-2000/HA-Alarms-and-Reminders/blob/main/configuration.yaml) to your `configuration.yaml` so, the sentences can work.",
            learn_more_url="https://www.home-assistant.io/voice_control/custom_sentences_yaml/",
        )
        _LOGGER.info(
            "Sentence files updated. Please reload 'All YAML configuration' in /developer-tools/yaml for changes to take effect."
        )

    _LOGGER.info(
        "Sentence files setup completed. Created %d files.", len(created_files)
    )


async def async_cleanup_sentence_files(hass: HomeAssistant) -> None:
    """Remove sentence files that were created during setup.

    This function:
    1. Retrieves the list of files created during setup
    2. Deletes each file
    3. Removes empty language directories
    4. Cleans up tracking data
    """
    created_files = hass.data.get(SENTENCE_FILES_KEY, [])

    if not created_files:
        _LOGGER.debug("No sentence files to clean up")
        return

    _LOGGER.info("Cleaning up %d sentence files", len(created_files))

    # Delete each created file
    for file_path_str in created_files:
        try:
            file_path = Path(file_path_str)
            if await hass.async_add_executor_job(file_path.exists):
                await hass.async_add_executor_job(file_path.unlink)
                _LOGGER.debug("Deleted sentence file: %s", file_path)

                # Try to remove parent directory if empty
                parent_dir = file_path.parent
                parent_exists = await hass.async_add_executor_job(parent_dir.exists)
                if parent_exists:
                    # Check if directory is empty
                    is_empty = not await hass.async_add_executor_job(
                        lambda: any(parent_dir.iterdir())
                    )
                    if is_empty:
                        await hass.async_add_executor_job(parent_dir.rmdir)
                        _LOGGER.debug("Removed empty directory: %s", parent_dir)

        except Exception as err:
            _LOGGER.debug("Error deleting sentence file %s: %s", file_path_str, err)

    # Clean up tracking data
    hass.data.pop(SENTENCE_FILES_KEY, None)
    hass.data.pop(SENTENCE_SETUP_KEY, None)

    # Delete the repair issue if it exists
    ir.async_delete_issue(hass, DOMAIN, "sentence_files_updated")

    _LOGGER.info("Sentence files cleanup completed")


