"""대시보드와 경보 규칙이 **실제로 있는 지표**를 보는지.

지표 이름을 바꾸면 코드는 멀쩡하고 검사도 통과하는데 패널만 조용히 빕니다. Grafana 는 없는
지표를 물어도 에러를 내지 않습니다. 그래서 설정 파일이 쓰는 이름을 등록부와 대조합니다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import main
from features.recommend.evaluation import diagnosis
from utils.metrics import REGISTRY

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
DASHBOARD = DEPLOY / "grafana/provisioning/dashboards/json/recommend_engine.json"
DATASOURCE = DEPLOY / "grafana/provisioning/datasources/prometheus.yml"
RULES = DEPLOY / "prometheus/rules/reco.yml"
PROMETHEUS = DEPLOY / "prometheus/prometheus.yml"
COMPOSE = DEPLOY / "docker-compose.yml"
METRIC = re.compile(r"\b((?:reco|http)_[a-z0-9_]+)\b")
#: Prometheus 가 스스로 만드는 지표. 등록부에는 없습니다.
BUILT_IN = {"up"}


def exported_names() -> set[str]:
    """등록부가 내보내는 표본 이름 전부. 아직 값이 없는 지표도 이름은 정해져 있습니다."""
    main.create_app()  # 내부 카운터 수집기는 앱이 뜰 때 등록됩니다
    names: set[str] = set()
    for family in REGISTRY.collect():
        if family.type == "counter":
            names.add(f"{family.name}_total")
        elif family.type == "histogram":
            names.update(f"{family.name}_{suffix}" for suffix in ("bucket", "sum", "count"))
        else:
            names.add(family.name)
    return names


def dashboard_expressions() -> list[str]:
    panels = json.loads(DASHBOARD.read_text(encoding="utf-8"))["panels"]
    return [target["expr"] for panel in panels for target in panel.get("targets", [])]


def rule_expressions() -> dict[str, str]:
    groups = yaml.safe_load(RULES.read_text(encoding="utf-8"))["groups"]
    return {rule["alert"]: rule["expr"] for group in groups for rule in group["rules"]}


def test_every_panel_asks_for_a_metric_that_exists() -> None:
    known = exported_names()
    expressions = dashboard_expressions()

    asked = {name for expr in expressions for name in METRIC.findall(expr)}

    assert len(expressions) >= 20
    assert asked - known == set(), (
        f"등록부에 없는 지표를 묻는 패널이 있습니다: {sorted(asked - known)}"
    )


def test_every_alert_asks_for_a_metric_that_exists() -> None:
    known = exported_names() | BUILT_IN

    for alert, expr in rule_expressions().items():
        asked = set(METRIC.findall(expr)) or {"up"}
        assert asked <= known, f"{alert}: {sorted(asked - known)}"


def test_the_alert_thresholds_are_the_ones_the_admin_page_uses() -> None:
    """같은 기준을 두 곳에 적었습니다. 한쪽만 고치면 경보와 관리자 페이지가 다른 말을 합니다."""
    rules = rule_expressions()

    assert rules["RecoDegradedHigh"].rstrip().endswith(f"> {diagnosis.DEGRADED_RATIO_LIMIT}")
    assert (
        rules["RecoContractViolations"].rstrip().endswith(f"> {diagnosis.VALIDATION_FAILURE_LIMIT}")
    )
    assert rules["RecoLatencyOverBudget"].rstrip().endswith(f"> {diagnosis.LATENCY_BUDGET:.0f}")


def test_the_dashboard_reads_the_provisioned_datasource() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    provisioned = yaml.safe_load(DATASOURCE.read_text(encoding="utf-8"))["datasources"][0]

    used = {panel["datasource"]["uid"] for panel in dashboard["panels"] if "datasource" in panel}

    assert used == {provisioned["uid"]}
    assert provisioned["type"] == "prometheus"
    assert len({panel["id"] for panel in dashboard["panels"]}) == len(dashboard["panels"])


def test_panels_read_rates_because_the_server_counts_from_zero_after_a_restart() -> None:
    """누적값을 그대로 그리면 재시작 때마다 절벽이 생기고, 그 절벽이 장애처럼 보입니다."""
    for expr in dashboard_expressions():
        assert "rate(" in expr or "increase(" in expr, expr


def test_the_scrape_config_never_holds_the_key() -> None:
    config = yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))
    job = config["scrape_configs"][0]

    assert job["metrics_path"] == "/metrics"
    assert job["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/internal_api_key",
    }
    assert "credentials" not in job["authorization"]


@pytest.mark.parametrize(
    "mounted",
    ["./prometheus/prometheus.yml", "./prometheus/rules", "./prometheus/targets"],
)
def test_the_compose_file_mounts_files_that_exist(mounted: str) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["prometheus"]

    assert any(volume.startswith(mounted + ":") for volume in service["volumes"])
    assert (DEPLOY / mounted).exists()
    assert compose["secrets"]["internal_api_key"] == {"environment": "INTERNAL_API_KEY"}
