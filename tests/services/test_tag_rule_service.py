import pytest

from app.models import FileInventory, FileTag, Operator, TagRule, TagRuleCriterionType
from app.services.tag_rule_service import TagRuleService


@pytest.mark.unit
class TestTagRuleService:
    def test_evaluate_extension(self, db_session):
        """Test evaluation of extension rules."""
        service = TagRuleService(db_session)

        # Rule for .txt
        rule = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value="txt"
        )

        assert service.evaluate_rule(rule, FileInventory(file_extension=".txt")) is True
        assert service.evaluate_rule(rule, FileInventory(file_extension=".TXT")) is True
        assert service.evaluate_rule(rule, FileInventory(file_extension=".jpg")) is False

        # Rule with dot
        rule_dot = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value=".txt"
        )
        assert service.evaluate_rule(rule_dot, FileInventory(file_extension=".txt")) is True

        # Contains
        rule_cont = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.CONTAINS, value="t"
        )
        assert service.evaluate_rule(rule_cont, FileInventory(file_extension=".txt")) is True

    def test_evaluate_path_pattern(self, db_session):
        """Test evaluation of path pattern rules."""
        service = TagRuleService(db_session)

        # Glob match
        rule_glob = TagRule(
            criterion_type=TagRuleCriterionType.PATH_PATTERN,
            operator=Operator.MATCHES,
            value="*/logs/*.log",
        )
        assert (
            service.evaluate_rule(rule_glob, FileInventory(file_path="/var/logs/app.log")) is True
        )
        assert (
            service.evaluate_rule(rule_glob, FileInventory(file_path="/home/user/test.txt"))
            is False
        )

        # Regex match
        rule_regex = TagRule(
            criterion_type=TagRuleCriterionType.PATH_PATTERN,
            operator=Operator.REGEX,
            value=r"\d{4}-\d{2}-\d{2}",
        )
        assert (
            service.evaluate_rule(
                rule_regex, FileInventory(file_path="/data/2023-01-01/report.pdf")
            )
            is True
        )

        # Contains
        rule_cont = TagRule(
            criterion_type=TagRuleCriterionType.PATH_PATTERN,
            operator=Operator.CONTAINS,
            value="backup",
        )
        assert (
            service.evaluate_rule(
                rule_cont, FileInventory(file_path="/mnt/storage/backup_file.zip")
            )
            is True
        )

    def test_evaluate_mime_type(self, db_session):
        """Test evaluation of MIME type rules."""
        service = TagRuleService(db_session)

        rule = TagRule(
            criterion_type=TagRuleCriterionType.MIME_TYPE,
            operator=Operator.MATCHES,
            value="image/*",
        )
        assert service.evaluate_rule(rule, FileInventory(mime_type="image/jpeg")) is True
        assert service.evaluate_rule(rule, FileInventory(mime_type="image/png")) is True
        assert service.evaluate_rule(rule, FileInventory(mime_type="text/plain")) is False

    def test_evaluate_size(self, db_session):
        """Test evaluation of size rules with various units."""
        service = TagRuleService(db_session)

        # Bytes
        rule_b = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="1000"
        )
        assert service.evaluate_rule(rule_b, FileInventory(file_size=1001)) is True
        assert service.evaluate_rule(rule_b, FileInventory(file_size=999)) is False

        # KB
        rule_kb = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GTE, value="1KB"
        )
        assert service.evaluate_rule(rule_kb, FileInventory(file_size=1024)) is True
        assert service.evaluate_rule(rule_kb, FileInventory(file_size=1023)) is False

        # MB
        rule_mb = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.LT, value="1 MB"
        )
        assert service.evaluate_rule(rule_mb, FileInventory(file_size=1024 * 1024 - 1)) is True

        # GB
        rule_gb = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="0.5GB"
        )
        assert service.evaluate_rule(rule_gb, FileInventory(file_size=1024 * 1024 * 1024)) is True

    def test_evaluate_name_pattern(self, db_session):
        """Test evaluation of name pattern rules."""
        service = TagRuleService(db_session)

        rule = TagRule(
            criterion_type=TagRuleCriterionType.NAME_PATTERN,
            operator=Operator.MATCHES,
            value="config_*",
        )
        assert (
            service.evaluate_rule(rule, FileInventory(file_path="/etc/app/config_v1.json")) is True
        )
        assert service.evaluate_rule(rule, FileInventory(file_path="/etc/app/other.json")) is False

    def test_apply_rule_to_file_skips_disabled_and_existing(
        self, db_session, file_inventory_factory, create_tag
    ):
        tag = create_tag("Skip Tag")
        disabled = TagRule(
            tag_id=tag.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="txt",
            enabled=False,
        )
        enabled = TagRule(
            tag_id=tag.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="txt",
            enabled=True,
        )
        db_session.add_all([disabled, enabled])
        db_session.commit()
        inv = file_inventory_factory(path="/tmp/skip.txt", file_extension=".txt")
        db_session.add(FileTag(file_id=inv.id, tag_id=tag.id, tagged_by="manual"))
        db_session.commit()

        service = TagRuleService(db_session)
        assert service.apply_rule_to_file(disabled, inv) is False
        assert service.apply_rule_to_file(enabled, inv) is False

    def test_apply_all_rules_no_enabled_rules(self, db_session):
        service = TagRuleService(db_session)
        stats = service.apply_all_rules()
        assert stats == {"files_processed": 0, "tags_added": 0}

    def test_apply_all_rules_commits_every_hundred_files(
        self, db_session, file_inventory_factory, create_tag, monkeypatch
    ):
        tag = create_tag("BatchCommit")
        rule = TagRule(
            tag_id=tag.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="nomatch",
            enabled=True,
        )
        db_session.add(rule)
        for i in range(100):
            file_inventory_factory(
                path=f"/tmp/batch_commit_{i}.zzz",
                file_extension=".zzz",
                path_name=f"batch_path_{i}",
            )
        db_session.commit()

        service = TagRuleService(db_session)
        commit_calls = 0
        original_commit = db_session.commit

        def counting_commit():
            nonlocal commit_calls
            commit_calls += 1
            return original_commit()

        monkeypatch.setattr(db_session, "commit", counting_commit)
        stats = service.apply_all_rules()
        assert stats["files_processed"] == 100
        assert commit_calls >= 2

    def test_apply_rules_to_file_with_prefetched_rules(
        self, db_session, file_inventory_factory, create_tag
    ):
        tag = create_tag("Prefetched")
        rule = TagRule(
            tag_id=tag.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="txt",
            enabled=True,
        )
        inv = file_inventory_factory(path="/tmp/prefetch.txt", file_extension=".txt")
        service = TagRuleService(db_session)
        added = service.apply_rules_to_file(inv, rules=[rule])
        assert added == 1

    def test_apply_rules_to_file(self, db_session, file_inventory_factory, create_tag):
        """Test applying rules to a single file and verifying DB update."""
        tag = create_tag("Auto Tag")
        rule = TagRule(
            tag_id=tag.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="txt",
            enabled=True,
        )
        db_session.add(rule)
        db_session.commit()

        inv = file_inventory_factory(path="/tmp/test_rule.txt", file_extension=".txt")
        service = TagRuleService(db_session)

        added_count = service.apply_rules_to_file(inv)

        assert added_count == 1
        # Verify FileTag was created
        ft = db_session.query(FileTag).filter_by(file_id=inv.id, tag_id=tag.id).first()
        assert ft is not None
        assert ft.tagged_by == "auto-rule"

    def test_apply_all_rules(self, db_session, file_inventory_factory, create_tag):
        """Test applying all enabled rules to all files."""
        tag1 = create_tag("Text")
        tag2 = create_tag("Large")

        rule1 = TagRule(
            tag_id=tag1.id,
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.EQ,
            value="txt",
            enabled=True,
        )
        rule2 = TagRule(
            tag_id=tag2.id,
            criterion_type=TagRuleCriterionType.SIZE,
            operator=Operator.GT,
            value="1MB",
            enabled=True,
        )
        db_session.add_all([rule1, rule2])

        # File 1: Matches rule 1
        inv1 = file_inventory_factory(path="/tmp/f1.txt", file_extension=".txt", size=100)
        # File 2: Matches rule 2
        inv2 = file_inventory_factory(
            path="/tmp/f2.jpg", file_extension=".jpg", size=2 * 1024 * 1024, path_name="p2"
        )
        # File 3: Matches both
        inv3 = file_inventory_factory(
            path="/tmp/f3.txt", file_extension=".txt", size=2 * 1024 * 1024, path_name="p3"
        )

        db_session.commit()

        service = TagRuleService(db_session)
        stats = service.apply_all_rules()

        assert stats["files_processed"] == 3
        assert stats["tags_added"] == 4  # 1 + 1 + 2

        assert db_session.query(FileTag).filter_by(file_id=inv1.id).count() == 1
        assert db_session.query(FileTag).filter_by(file_id=inv2.id).count() == 1
        assert db_session.query(FileTag).filter_by(file_id=inv3.id).count() == 2

    def test_parse_size_invalid(self, db_session):
        """Test parsing invalid size strings."""
        service = TagRuleService(db_session)
        with pytest.raises(ValueError, match="Invalid size format"):
            service._parse_size("not-a-size")
        with pytest.raises(ValueError, match="Invalid size format"):
            service._parse_size("10XX")

    def test_evaluate_rule_edge_cases(self, db_session):
        from unittest.mock import patch

        service = TagRuleService(db_session)
        rule = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value="txt"
        )
        with patch.object(service, "_evaluate_extension", side_effect=RuntimeError("boom")):
            assert service.evaluate_rule(rule, FileInventory(file_extension=".txt")) is False

        no_ext = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value="txt"
        )
        assert service.evaluate_rule(no_ext, FileInventory(file_extension=None)) is False

        bad_regex = TagRule(
            criterion_type=TagRuleCriterionType.PATH_PATTERN,
            operator=Operator.REGEX,
            value="[invalid",
        )
        assert service.evaluate_rule(bad_regex, FileInventory(file_path="/tmp/x.txt")) is False

        mime_wildcard = TagRule(
            criterion_type=TagRuleCriterionType.MIME_TYPE,
            operator=Operator.MATCHES,
            value="text/*",
        )
        assert service.evaluate_rule(mime_wildcard, FileInventory(mime_type="text/plain")) is True

        size_lte = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.LTE, value="10"
        )
        assert service.evaluate_rule(size_lte, FileInventory(file_size=10)) is True
        assert service.evaluate_rule(size_lte, FileInventory(file_size=11)) is False

        size_unknown_op = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.CONTAINS, value="10"
        )
        assert service.evaluate_rule(size_unknown_op, FileInventory(file_size=100)) is False

        bad_size_rule = TagRule(
            criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="not-a-size"
        )
        assert service.evaluate_rule(bad_size_rule, FileInventory(file_size=100)) is False

        name_regex = TagRule(
            criterion_type=TagRuleCriterionType.NAME_PATTERN,
            operator=Operator.REGEX,
            value="cfg_\\d+",
        )
        assert (
            service.evaluate_rule(name_regex, FileInventory(file_path="/tmp/cfg_42.json")) is True
        )

        from unittest.mock import MagicMock

        unknown = MagicMock()
        unknown.id = 99
        unknown.criterion_type = "NOT_A_REAL_TYPE"
        assert service.evaluate_rule(unknown, FileInventory(file_extension=".txt")) is False

        ext_other_op = TagRule(
            criterion_type=TagRuleCriterionType.EXTENSION,
            operator=Operator.GT,
            value="txt",
        )
        assert service.evaluate_rule(ext_other_op, FileInventory(file_extension=".txt")) is False

        mime_eq = TagRule(
            criterion_type=TagRuleCriterionType.MIME_TYPE,
            operator=Operator.EQ,
            value="text/plain",
        )
        assert service.evaluate_rule(mime_eq, FileInventory(mime_type="text/plain")) is True

        path_other = TagRule(
            criterion_type=TagRuleCriterionType.PATH_PATTERN,
            operator=Operator.GT,
            value="/tmp",
        )
        assert service.evaluate_rule(path_other, FileInventory(file_path="/tmp/x")) is False

        name_contains = TagRule(
            criterion_type=TagRuleCriterionType.NAME_PATTERN,
            operator=Operator.CONTAINS,
            value="cfg",
        )
        assert (
            service.evaluate_rule(name_contains, FileInventory(file_path="/tmp/my_cfg.json"))
            is True
        )

        bad_mime_wildcard = TagRule(
            criterion_type=TagRuleCriterionType.MIME_TYPE,
            operator=Operator.MATCHES,
            value="[bad",
        )
        assert (
            service.evaluate_rule(bad_mime_wildcard, FileInventory(mime_type="text/plain")) is False
        )

        name_other_op = TagRule(
            criterion_type=TagRuleCriterionType.NAME_PATTERN,
            operator=Operator.GT,
            value="x",
        )
        assert service.evaluate_rule(name_other_op, FileInventory(file_path="/tmp/x.txt")) is False
