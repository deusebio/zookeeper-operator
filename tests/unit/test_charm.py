#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CHARM_KEY, PEER

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation("restart", CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_install_fails_create_passwords_until_peer_relation(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("snap.ZooKeeperSnap.install"):
        harness.charm.on.install.emit()

    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    assert not harness.charm.cluster.relation.data[harness.charm.app].get("sync-password", None)
    assert not harness.charm.cluster.relation.data[harness.charm.app].get("super-password", None)


def test_install_fails_creates_passwords_succeeds(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    with patch("snap.ZooKeeperSnap.install"):
        harness.charm.on.install.emit()

        assert harness.charm.cluster.relation.data[harness.charm.app].get("sync-password", None)
        assert harness.charm.cluster.relation.data[harness.charm.app].get("super-password", None)


def test_install_blocks_snap_install_failure(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    with patch("snap.ZooKeeperSnap.install", return_value=False):
        harness.charm.on.install.emit()

        assert isinstance(harness.model.unit.status, BlockedStatus)


def test_relation_changed_emitted_for_leader_elected(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed") as patched:
        harness.set_leader(True)
        patched.assert_called_once()


def test_relation_changed_emitted_for_config_changed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_changed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_changed.emit(harness.charm.cluster.relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_joined(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_joined.emit(harness.charm.cluster.relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_departed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_departed.emit(harness.charm.cluster.relation)
        patched.assert_called_once()


def test_relation_changed_waits_until_peer_relation(harness):
    harness.charm.on.config_changed.emit()
    assert isinstance(harness.model.unit.status, WaitingStatus)


def test_relation_changed_stops_if_not_rotate_passwords(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"rotate-passwords": "true"})
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"password-rotated": "true"})
    with patch("charm.ZooKeeperCharm.update_quorum") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_not_called()


def test_relation_changed_starts_units(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm.init_server") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_does_not_start_units_again(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
    with patch("charm.ZooKeeperCharm.init_server") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_not_called()


def test_relation_changed_does_not_restart_on_departing(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock") as patched:
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        patched.assert_not_called()


def test_relation_changed_updates_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charm.ZooKeeperCharm.update_quorum") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_restarts(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_defers_upgrading_single_unit(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"upgrading": "started"})
    with patch("ops.framework.EventBase.defer") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_restart_fails_not_started(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    with patch("snap.ZooKeeperSnap.restart_snap_service") as patched:
        harness.charm._restart(EventBase)
        patched.assert_not_called()


def test_restart_restarts_snap_service_if_config_changed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with patch("snap.ZooKeeperSnap.restart_snap_service") as patched, patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):

        harness.charm._restart(EventBase)
        patched.assert_called_once()


def test_restart_restarts_snap_service_if_manual(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"manual-restart": "true"})

    with patch("snap.ZooKeeperSnap.restart_snap_service") as patched, patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        patched.assert_called_once()


def test_restart_restarts_snap_service_sleeps(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep") as patched:
        harness.charm._restart(EventBase)
        patched.assert_called_once()


def test_restart_restarts_snap_sets_active_status(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert isinstance(harness.model.unit.status, ActiveStatus)


def test_restart_sets_password_rotated_on_unit(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"rotate-passwords": "true"})

    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert (
            harness.charm.cluster.relation.data[harness.charm.unit].get("password-rotated", None)
            == "true"
        )


def test_restart_sets_unified(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"upgrading": "started"})
    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert (
            harness.charm.cluster.relation.data[harness.charm.unit].get("unified", None) == "true"
        )

    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"upgrading": ""})
    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert not harness.charm.cluster.relation.data[harness.charm.unit].get("unified", None)


def test_restart_unsets_manual_restart(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(
        peer_rel_id, f"{CHARM_KEY}/0", {"state": "started", "manual-restart": "true"}
    )

    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed"
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert not harness.charm.cluster.relation.data[harness.charm.unit].get("unified", None)


def test_restart_sets_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"quorum": "ssl"})
    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert harness.charm.cluster.relation.data[harness.charm.unit].get("quorum", None) == "ssl"

    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"quorum": ""})
    with patch("snap.ZooKeeperSnap.restart_snap_service"), patch(
        "charm.ZooKeeperCharm.config_changed", return_value=True
    ), patch("time.sleep"):
        harness.charm._restart(EventBase)
        assert not harness.charm.cluster.relation.data[harness.charm.unit].get("quorum", None)


def test_init_server_maintenance_if_no_passwords(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    harness.charm.init_server()

    assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_init_server_maintenance_if_not_turn(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(
        peer_rel_id, CHARM_KEY, {"sync-password": "mellon", "super-password": "mellon"}
    )

    with patch("cluster.ZooKeeperCluster.is_unit_turn", return_value=False):
        harness.charm.init_server()

        assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_init_server_calls_necessary_methods(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"private-address": "gimli"})
    harness.update_relation_data(
        peer_rel_id,
        CHARM_KEY,
        {
            "sync-password": "mellon",
            "super-password": "mellon",
            "upgrading": "started",
            "quorum": "ssl",
        },
    )
    with patch("cluster.ZooKeeperCluster.is_unit_turn", return_value=True), patch(
        "config.ZooKeeperConfig.set_zookeeper_myid"
    ) as zookeeper_myid, patch(
        "config.ZooKeeperConfig.set_server_jvmflags"
    ) as server_jvmflags, patch(
        "config.ZooKeeperConfig.set_zookeeper_dynamic_properties"
    ) as zookeeper_dynamic_properties, patch(
        "config.ZooKeeperConfig.set_zookeeper_properties"
    ) as zookeeper_properties, patch(
        "config.ZooKeeperConfig.set_jaas_config"
    ) as zookeeper_jaas_config, patch(
        "snap.ZooKeeperSnap.restart_snap_service"
    ) as restart:
        harness.charm.init_server()

        zookeeper_myid.assert_called_once()
        server_jvmflags.assert_called_once()
        zookeeper_dynamic_properties.assert_called_once()
        zookeeper_properties.assert_called_once()
        zookeeper_jaas_config.assert_called_once()
        restart.assert_called_once()

        assert harness.charm.cluster.relation.data[harness.charm.unit].get("quorum", None) == "ssl"
        assert (
            harness.charm.cluster.relation.data[harness.charm.unit].get("unified", None) == "true"
        )
        assert (
            harness.charm.cluster.relation.data[harness.charm.unit].get("state", None) == "started"
        )

        assert isinstance(harness.charm.unit.status, ActiveStatus)


def test_config_changed_updates_properties_and_jaas(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch(
        "config.ZooKeeperConfig.build_static_properties", return_value=["gandalf=white"]
    ), patch("config.ZooKeeperConfig.static_properties", return_value="gandalf=grey"), patch(
        "config.ZooKeeperConfig.jaas_config", return_value=""
    ), patch(
        "config.ZooKeeperConfig.set_zookeeper_properties"
    ) as set_props, patch(
        "config.ZooKeeperConfig.set_jaas_config"
    ) as set_jaas:
        harness.charm.config_changed()
        set_props.assert_called_once()
        set_jaas.assert_not_called()

    with patch("config.ZooKeeperConfig.jaas_config", return_value="gandalf=white"), patch(
        "charm.safe_get_file", return_value=["gandalf=grey"]
    ), patch("config.ZooKeeperConfig.build_static_properties", return_value=[]), patch(
        "config.ZooKeeperConfig.set_zookeeper_properties"
    ) as set_props, patch(
        "config.ZooKeeperConfig.set_jaas_config"
    ) as set_jaas:
        harness.charm.config_changed()
        set_props.assert_not_called()
        set_jaas.assert_called_once()


def test_adding_units_updates_relation_data(harness):
    with (patch("cluster.ZooKeeperCluster.update_cluster", return_value={"1": "added"})):
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/1", {"quorum": "ssl"})

        assert harness.charm.cluster.relation.data[harness.charm.app].get("1", None) == "added"


def test_update_quorum_skips_relation_departed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charm.ZooKeeperCharm.add_init_leader") as patched_init_leader, patch(
        "cluster.ZooKeeperCluster.update_cluster"
    ) as patched_update_cluster:
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        patched_init_leader.assert_not_called()
        patched_update_cluster.assert_not_called()


def test_update_quorum_updates_cluster_for_relation_departed(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
    harness.set_leader(True)

    with patch("cluster.ZooKeeperCluster.update_cluster") as patched_update_cluster:
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        patched_update_cluster.assert_called()


def test_update_quorum_updates_cluster_for_leader_elected(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    with patch("cluster.ZooKeeperCluster.update_cluster") as patched_update_cluster:
        harness.set_leader(True)
        patched_update_cluster.assert_called()


def test_update_quorum_adds_init_leader(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    with patch("charm.ZooKeeperCharm.add_init_leader") as patched_init_leader:
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

        patched_init_leader.assert_called_once()


def test_update_quorum_sets_non_ssl_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness.set_leader(True)

    assert harness.charm.cluster.relation.data[harness.charm.app].get("quorum", None) == "non-ssl"


def test_update_quorum_sets_ssl_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.update_relation_data(peer_rel_id, CHARM_KEY, {"tls": "enabled"})
    harness.set_leader(True)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    assert harness.charm.cluster.relation.data[harness.charm.app].get("quorum", None) == "ssl"


def test_update_quorum_does_not_set_ssl_quorum_until_unified(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.set_leader(True)
    with harness.hooks_disabled():
        harness.update_relation_data(peer_rel_id, CHARM_KEY, {"tls": "enabled"})
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"unified": ""})

    assert not harness.charm.cluster.relation.data[harness.charm.app].get("quorum", None) == "ssl"


def test_update_quorum_does_not_unset_upgrading_until_all_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.set_leader(True)
    with harness.hooks_disabled():
        harness.update_relation_data(
            peer_rel_id, CHARM_KEY, {"tls": "enabled", "upgrading": "started", "quorum": "non-ssl"}
        )
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"quorum": "non-ssl"})

    assert not harness.charm.cluster.relation.data[harness.charm.app].get("quorum", None) == "ssl"


def test_update_quorum_unsets_upgrading_when_all_quorum(harness):
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.set_leader(True)
    with harness.hooks_disabled():
        harness.update_relation_data(
            peer_rel_id, CHARM_KEY, {"tls": "enabled", "upgrading": "started", "quorum": "ssl"}
        )
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/1", {"quorum": "ssl"})

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"quorum": "ssl"})

    assert harness.charm.cluster.relation.data[harness.charm.app].get("quorum", None) == "ssl"


def test_config_changed_applies_relation_data(harness):
    _ = harness.add_relation(PEER, CHARM_KEY)
    harness.set_leader(True)

    with patch("provider.ZooKeeperProvider.apply_relation_data", return_value=None) as patched:
        harness.charm.on.config_changed.emit()

        patched.assert_called_once()


def test_init_leader_is_added(harness):
    with patch("charm.ZooKeeperCharm.config_changed", return_value=True):
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)

        assert harness.charm.cluster.relation.data[harness.charm.app].get("0", None) == "added"
