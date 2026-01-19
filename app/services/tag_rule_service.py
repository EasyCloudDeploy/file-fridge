"""Service for evaluating and applying tag rules to files."""

import fnmatch
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import FileInventory, FileTag, Operator, TagRule, TagRuleCriterionType

logger = logging.getLogger(__name__)


class TagRuleService:
    """Service for evaluating and applying tag rules."""

    def __init__(self, db: Session):
        self.db = db

    def evaluate_rule(self, rule: TagRule, file: FileInventory) -> bool:
        """
        Evaluate if a tag rule matches a file.

        Args:
            rule: The tag rule to evaluate
            file: The file to check against

        Returns:
            True if the rule matches the file, False otherwise
        """
        try:
            if rule.criterion_type == TagRuleCriterionType.EXTENSION:
                return self._evaluate_extension(rule, file)
            if rule.criterion_type == TagRuleCriterionType.PATH_PATTERN:
                return self._evaluate_path_pattern(rule, file)
            if rule.criterion_type == TagRuleCriterionType.MIME_TYPE:
                return self._evaluate_mime_type(rule, file)
            if rule.criterion_type == TagRuleCriterionType.SIZE:
                return self._evaluate_size(rule, file)
            if rule.criterion_type == TagRuleCriterionType.NAME_PATTERN:
                return self._evaluate_name_pattern(rule, file)
            logger.warning(f"Unknown criterion type: {rule.criterion_type}")
            return False
        except Exception:
            logger.exception(f"Error evaluating rule {rule.id}")
            return False

    def _evaluate_extension(self, rule: TagRule, file: FileInventory) -> bool:
        """Evaluate extension-based rule."""
        if not file.file_extension:
            return False

        file_ext = file.file_extension.lower()
        rule_ext = rule.value.lower()

        # Ensure extension starts with dot
        if not rule_ext.startswith("."):
            rule_ext = f".{rule_ext}"

        if rule.operator in (Operator.EQ, Operator.MATCHES):
            return file_ext == rule_ext
        if rule.operator == Operator.CONTAINS:
            return rule_ext in file_ext
        return False

    def _evaluate_path_pattern(self, rule: TagRule, file: FileInventory) -> bool:
        """Evaluate path pattern rule (glob or regex)."""
        if not file.file_path:
            return False

        if rule.operator == Operator.MATCHES:
            # Glob pattern matching
            return fnmatch.fnmatch(file.file_path, rule.value)
        if rule.operator == Operator.REGEX:
            # Regex pattern matching
            try:
                pattern = re.compile(rule.value)
                return bool(pattern.search(file.file_path))
            except re.error:
                logger.exception(f"Invalid regex pattern in rule {rule.id}")
                return False
        elif rule.operator == Operator.CONTAINS:
            # Simple substring match
            return rule.value in file.file_path
        else:
            return False

    def _evaluate_mime_type(self, rule: TagRule, file: FileInventory) -> bool:
        """Evaluate MIME type rule."""
        if not file.mime_type:
            return False

        file_mime = file.mime_type.lower()
        rule_mime = rule.value.lower()

        if rule.operator == Operator.EQ:
            return file_mime == rule_mime
        if rule.operator == Operator.CONTAINS:
            return rule_mime in file_mime
        if rule.operator == Operator.MATCHES:
            # Support wildcard patterns like "image/*"
            if "*" in rule_mime:
                pattern = rule_mime.replace("*", ".*")
                try:
                    return bool(re.match(f"^{pattern}$", file_mime))
                except re.error:
                    return False
            return file_mime == rule_mime
        return False

    def _evaluate_size(self, rule: TagRule, file: FileInventory) -> bool:
        """Evaluate file size rule."""
        if file.file_size is None:
            return False

        try:
            # Parse size value (support KB, MB, GB suffixes)
            size_value = self._parse_size(rule.value)

            if rule.operator == Operator.GT:
                return file.file_size > size_value
            if rule.operator == Operator.LT:
                return file.file_size < size_value
            if rule.operator == Operator.EQ:
                return file.file_size == size_value
            if rule.operator == Operator.GTE:
                return file.file_size >= size_value
            if rule.operator == Operator.LTE:
                return file.file_size <= size_value
            return False
        except ValueError:
            logger.exception(f"Invalid size value in rule {rule.id}")
            return False

    def _evaluate_name_pattern(self, rule: TagRule, file: FileInventory) -> bool:
        """Evaluate filename pattern rule (not full path)."""
        if not file.file_path:
            return False

        filename = Path(file.file_path).name

        if rule.operator == Operator.MATCHES:
            # Glob pattern matching
            return fnmatch.fnmatch(filename, rule.value)
        if rule.operator == Operator.REGEX:
            # Regex pattern matching
            try:
                pattern = re.compile(rule.value)
                return bool(pattern.search(filename))
            except re.error:
                logger.exception(f"Invalid regex pattern in rule {rule.id}")
                return False
        elif rule.operator == Operator.CONTAINS:
            # Simple substring match
            return rule.value in filename
        else:
            return False

    def _parse_size(self, size_str: str) -> int:
        """
        Parse size string to bytes.
        Supports: 100, 100B, 10KB, 5MB, 2GB
        """
        size_str = size_str.strip().upper()

        # Extract number and unit
        match = re.match(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?$", size_str)
        if not match:
            msg = f"Invalid size format: {size_str}"
            raise ValueError(msg)

        number = float(match.group(1))
        unit = match.group(2) or "B"

        multipliers = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}

        return int(number * multipliers[unit])

    def apply_rule_to_file(self, rule: TagRule, file: FileInventory) -> bool:
        """
        Apply a single rule to a file (add tag if matches and not already tagged).

        Returns:
            True if tag was added, False otherwise
        """
        if not rule.enabled:
            return False

        # Check if rule matches
        if not self.evaluate_rule(rule, file):
            return False

        # Check if file already has this tag
        existing_tag = (
            self.db.query(FileTag)
            .filter(FileTag.file_id == file.id, FileTag.tag_id == rule.tag_id)
            .first()
        )

        if existing_tag:
            return False  # Already tagged

        # Add tag
        file_tag = FileTag(file_id=file.id, tag_id=rule.tag_id, tagged_by="auto-rule")
        self.db.add(file_tag)
        return True

    def apply_rules_to_file(
        self, file: FileInventory, rules: Optional[list[TagRule]] = None
    ) -> int:
        """
        Apply rules to a single file.

        Args:
            file: The file to tag
            rules: Optional pre-fetched list of enabled rules. If None, rules will be fetched from DB.

        Returns:
            Number of tags added
        """
        if rules is None:
            # Get all enabled rules ordered by priority
            rules = (
                self.db.query(TagRule)
                .filter(TagRule.enabled)
                .order_by(TagRule.priority.desc(), TagRule.created_at.asc())
                .all()
            )

        tags_added = 0
        for rule in rules:
            if self.apply_rule_to_file(rule, file):
                tags_added += 1

        if tags_added > 0:
            self.db.commit()

        return tags_added

    def apply_all_rules(self) -> Dict[str, int]:
        """
        Apply all enabled tag rules to all files in inventory.

        Returns:
            Dictionary with statistics (files_processed, tags_added)
        """
        logger.info("Applying all tag rules to file inventory...")

        # Get all enabled rules
        rules = (
            self.db.query(TagRule)
            .filter(TagRule.enabled)
            .order_by(TagRule.priority.desc(), TagRule.created_at.asc())
            .all()
        )

        if not rules:
            logger.info("No enabled tag rules found")
            return {"files_processed": 0, "tags_added": 0}

        # Get all files
        files = self.db.query(FileInventory).all()

        files_processed = 0
        tags_added = 0

        for file in files:
            for rule in rules:
                if self.apply_rule_to_file(rule, file):
                    tags_added += 1
            files_processed += 1

            # Commit periodically to avoid long transactions
            if files_processed % 100 == 0:
                self.db.commit()
                logger.info(f"Processed {files_processed} files, added {tags_added} tags")

        # Final commit
        self.db.commit()

        logger.info(
            f"Tag rule application complete: {files_processed} files processed, {tags_added} tags added"
        )
        return {"files_processed": files_processed, "tags_added": tags_added}
