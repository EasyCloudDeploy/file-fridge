import pytest

from app.models import FileInventory, TagRule, TagRuleCriterionType, Operator, Tag, FileTag
from app.services.tag_rule_service import TagRuleService


@pytest.mark.unit
class TestTagRuleService:
    def test_evaluate_extension(self, db_session):
        """Test evaluation of extension rules."""
        service = TagRuleService(db_session)
        
        # Rule for .txt
        rule = TagRule(criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value="txt")
        
        assert service.evaluate_rule(rule, FileInventory(file_extension=".txt")) is True
        assert service.evaluate_rule(rule, FileInventory(file_extension=".TXT")) is True
        assert service.evaluate_rule(rule, FileInventory(file_extension=".jpg")) is False
        
        # Rule with dot
        rule_dot = TagRule(criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value=".txt")
        assert service.evaluate_rule(rule_dot, FileInventory(file_extension=".txt")) is True
        
        # Contains
        rule_cont = TagRule(criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.CONTAINS, value="t")
        assert service.evaluate_rule(rule_cont, FileInventory(file_extension=".txt")) is True

    def test_evaluate_path_pattern(self, db_session):
        """Test evaluation of path pattern rules."""
        service = TagRuleService(db_session)
        
        # Glob match
        rule_glob = TagRule(criterion_type=TagRuleCriterionType.PATH_PATTERN, operator=Operator.MATCHES, value="*/logs/*.log")
        assert service.evaluate_rule(rule_glob, FileInventory(file_path="/var/logs/app.log")) is True
        assert service.evaluate_rule(rule_glob, FileInventory(file_path="/home/user/test.txt")) is False
        
        # Regex match
        rule_regex = TagRule(criterion_type=TagRuleCriterionType.PATH_PATTERN, operator=Operator.REGEX, value=r"\d{4}-\d{2}-\d{2}")
        assert service.evaluate_rule(rule_regex, FileInventory(file_path="/data/2023-01-01/report.pdf")) is True
        
        # Contains
        rule_cont = TagRule(criterion_type=TagRuleCriterionType.PATH_PATTERN, operator=Operator.CONTAINS, value="backup")
        assert service.evaluate_rule(rule_cont, FileInventory(file_path="/mnt/storage/backup_file.zip")) is True

    def test_evaluate_mime_type(self, db_session):
        """Test evaluation of MIME type rules."""
        service = TagRuleService(db_session)
        
        rule = TagRule(criterion_type=TagRuleCriterionType.MIME_TYPE, operator=Operator.MATCHES, value="image/*")
        assert service.evaluate_rule(rule, FileInventory(mime_type="image/jpeg")) is True
        assert service.evaluate_rule(rule, FileInventory(mime_type="image/png")) is True
        assert service.evaluate_rule(rule, FileInventory(mime_type="text/plain")) is False

    def test_evaluate_size(self, db_session):
        """Test evaluation of size rules with various units."""
        service = TagRuleService(db_session)
        
        # Bytes
        rule_b = TagRule(criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="1000")
        assert service.evaluate_rule(rule_b, FileInventory(file_size=1001)) is True
        assert service.evaluate_rule(rule_b, FileInventory(file_size=999)) is False
        
        # KB
        rule_kb = TagRule(criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GTE, value="1KB")
        assert service.evaluate_rule(rule_kb, FileInventory(file_size=1024)) is True
        assert service.evaluate_rule(rule_kb, FileInventory(file_size=1023)) is False
        
        # MB
        rule_mb = TagRule(criterion_type=TagRuleCriterionType.SIZE, operator=Operator.LT, value="1 MB")
        assert service.evaluate_rule(rule_mb, FileInventory(file_size=1024*1024 - 1)) is True
        
        # GB
        rule_gb = TagRule(criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="0.5GB")
        assert service.evaluate_rule(rule_gb, FileInventory(file_size=1024*1024*1024)) is True

    def test_evaluate_name_pattern(self, db_session):
        """Test evaluation of name pattern rules."""
        service = TagRuleService(db_session)
        
        rule = TagRule(criterion_type=TagRuleCriterionType.NAME_PATTERN, operator=Operator.MATCHES, value="config_*")
        assert service.evaluate_rule(rule, FileInventory(file_path="/etc/app/config_v1.json")) is True
        assert service.evaluate_rule(rule, FileInventory(file_path="/etc/app/other.json")) is False

    def test_apply_rules_to_file(self, db_session, file_inventory_factory, create_tag):
        """Test applying rules to a single file and verifying DB update."""
        tag = create_tag("Auto Tag")
        rule = TagRule(
            tag_id=tag.id, 
            criterion_type=TagRuleCriterionType.EXTENSION, 
            operator=Operator.EQ, 
            value="txt", 
            enabled=True
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
        
        rule1 = TagRule(tag_id=tag1.id, criterion_type=TagRuleCriterionType.EXTENSION, operator=Operator.EQ, value="txt", enabled=True)
        rule2 = TagRule(tag_id=tag2.id, criterion_type=TagRuleCriterionType.SIZE, operator=Operator.GT, value="1MB", enabled=True)
        db_session.add_all([rule1, rule2])
        
        # File 1: Matches rule 1
        inv1 = file_inventory_factory(path="/tmp/f1.txt", file_extension=".txt", size=100)
        # File 2: Matches rule 2
        inv2 = file_inventory_factory(path="/tmp/f2.jpg", file_extension=".jpg", size=2*1024*1024, path_name="p2")
        # File 3: Matches both
        inv3 = file_inventory_factory(path="/tmp/f3.txt", file_extension=".txt", size=2*1024*1024, path_name="p3")
        
        db_session.commit()
        
        service = TagRuleService(db_session)
        stats = service.apply_all_rules()
        
        assert stats["files_processed"] == 3
        assert stats["tags_added"] == 4 # 1 + 1 + 2
        
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
