"""The two supported buildout workflows.

Both share the same site-preparation skeleton and diverge at the point where the
rack is populated, which gives them genuinely different critical paths rather
than being the same DAG with renamed tasks.

Durations are in simulated minutes.
"""

from app.critical_path_evaluator import build_workflow
from app.models import TaskDef, WorkflowDef

# Site preparation, identical for both buildout types.
_SHARED_PREP = (
    TaskDef(id="site_survey", label="Site survey", duration_min=30),
    TaskDef(id="rack_delivery", label="Rack delivery", duration_min=60),
    TaskDef(
        id="rack_placement",
        label="Rack placement",
        duration_min=45,
        parent_task_ids=("site_survey", "rack_delivery"),
    ),
    TaskDef(
        id="power_whip_install",
        label="Power whip install",
        duration_min=90,
        parent_task_ids=("site_survey",),
    ),
    TaskDef(
        id="network_fabric_pull",
        label="Network fabric pull",
        duration_min=120,
        parent_task_ids=("site_survey",),
    ),
    TaskDef(
        id="pdu_install",
        label="PDU install",
        duration_min=40,
        parent_task_ids=("rack_placement", "power_whip_install"),
    ),
    TaskDef(
        id="tor_switch_install",
        label="ToR switch install",
        duration_min=50,
        parent_task_ids=("rack_placement", "network_fabric_pull"),
    ),
)

COMPUTE_RACK = build_workflow(
    "compute_rack",
    "Compute rack buildout",
    _SHARED_PREP
    + (
        TaskDef(
            id="gpu_node_install",
            label="GPU node install",
            duration_min=180,
            parent_task_ids=("rack_placement", "pdu_install"),
        ),
        TaskDef(
            id="node_cabling",
            label="Node cabling",
            duration_min=70,
            parent_task_ids=("gpu_node_install", "tor_switch_install"),
        ),
        TaskDef(
            id="firmware_flash",
            label="Firmware flash",
            duration_min=60,
            parent_task_ids=("node_cabling",),
        ),
        TaskDef(
            id="burn_in_test",
            label="Burn-in test",
            duration_min=150,
            parent_task_ids=("firmware_flash",),
        ),
        TaskDef(
            id="network_validation",
            label="Network validation",
            duration_min=45,
            parent_task_ids=("node_cabling",),
        ),
        TaskDef(
            id="capacity_handoff",
            label="Capacity handoff",
            duration_min=20,
            parent_task_ids=("burn_in_test", "network_validation"),
        ),
    ),
)

STORAGE_RACK = build_workflow(
    "storage_rack",
    "Storage rack buildout",
    _SHARED_PREP
    + (
        TaskDef(
            id="jbod_shelf_install",
            label="JBOD shelf install",
            duration_min=150,
            parent_task_ids=("rack_placement", "pdu_install"),
        ),
        TaskDef(
            id="disk_population",
            label="Disk population",
            duration_min=200,
            parent_task_ids=("jbod_shelf_install",),
        ),
        TaskDef(
            id="node_cabling",
            label="Node cabling",
            duration_min=70,
            parent_task_ids=("disk_population", "tor_switch_install"),
        ),
        TaskDef(
            id="firmware_flash",
            label="Firmware flash",
            duration_min=60,
            parent_task_ids=("node_cabling",),
        ),
        TaskDef(
            id="array_init",
            label="Array init",
            duration_min=180,
            parent_task_ids=("firmware_flash",),
        ),
        TaskDef(
            id="network_validation",
            label="Network validation",
            duration_min=45,
            parent_task_ids=("node_cabling",),
        ),
        TaskDef(
            id="capacity_handoff",
            label="Capacity handoff",
            duration_min=20,
            parent_task_ids=("array_init", "network_validation"),
        ),
    ),
)

WORKFLOWS: dict[str, WorkflowDef] = {
    COMPUTE_RACK.id: COMPUTE_RACK,
    STORAGE_RACK.id: STORAGE_RACK,
}
