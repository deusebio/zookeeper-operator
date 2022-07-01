# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from collections import defaultdict
import logging
from typing import Dict, List, Optional, Set
from kazoo.handlers.threading import KazooTimeoutError
from kazoo.security import ACL, make_acl
from ops.charm import RelationBrokenEvent, RelationEvent

from ops.framework import Object
from ops.model import MaintenanceStatus, Relation

from charms.zookeeper.v0.client import (
    MemberNotReadyError,
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperManager,
)
from charms.zookeeper.v0.cluster import UnitNotFoundError, ZooKeeperCluster

logger = logging.getLogger(__name__)

REL_NAME = "database"
PEER = "cluster"


class ZooKeeperProvider(Object):
    def __init__(self, charm) -> None:
        super().__init__(charm, "client")

        self.charm = charm

        self.framework.observe(
            self.charm.on[REL_NAME].relation_joined, self._on_client_relation_updated
        )
        self.framework.observe(
            self.charm.on[REL_NAME].relation_changed, self._on_client_relation_updated
        )
        self.framework.observe(
            self.charm.on[REL_NAME].relation_broken, self._on_client_relation_broken
        )

    @property
    def app_relation(self) -> Relation:
        """Gets the current ZK peer relation."""
        return self.charm.model.get_relation(PEER)

    @property
    def client_relations(self) -> List[Relation]:
        """Gets the relations for all related client applications."""
        return self.charm.model.relations[REL_NAME]

    def relation_config(
        self, relation: Relation, event: Optional[RelationEvent] = None
    ) -> Optional[Dict[str, str]]:
        """Gets the auth config for a currently related application.

        Args:
            relation: the relation you want to build config for
            event (optional): the corresponding event.
                If passed and is `RelationBrokenEvent`, will skip and return `None`

        Returns:
            Dict containing relation `username`, `password`, `chroot` and `acl`

            `None` if `RelationBrokenEvent` is passed as event
        """

        # If RelationBrokenEvent, skip, we don't want it in the live-data
        if isinstance(event, RelationBrokenEvent):
            return None

        # generating username
        relation_id = relation.id
        username = f"relation-{relation_id}"

        # Default to empty string in case passwords not set
        password = self.app_relation.data[self.charm.app].get(username, "")

        # Default to full permissions if not set by the app
        acl = relation.data[relation.app].get("chroot-acl", "cdrwa")

        # Attempt to default to `database` app value. Else None, it's unset
        chroot = relation.data[relation.app].get(
            "chroot", relation.data[relation.app].get("database", "")
        )

        # If chroot is unset, skip, we don't want it part of the config
        if not chroot:
            logger.info("CHROOT NOT SET")
            return None

        if not str(chroot).startswith("/"):
            chroot = f"/{chroot}"

        return {"username": username, "password": password, "chroot": chroot, "acl": acl}

    def relations_config(self, event: Optional[RelationEvent] = None) -> Dict[str, Dict[str, str]]:
        """Gets auth configs for all currently related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Dict of key = `relation_id`, value = `relations_config()` for all related apps

        """
        relations_config = {}

        for relation in self.client_relations:
            config = self.relation_config(relation=relation, event=event)

            # in the case of RelationBroken or unset chroot
            if not config:
                continue

            relation_id: int = relation.id
            relations_config[str(relation_id)] = config

        return relations_config

    def build_acls(self, event: Optional[RelationEvent]) -> Dict[str, List[ACL]]:
        """Gets ACLs for all currently related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Dict of `chroot`s with value as list of ACLs for the `chroot`
        """
        acls = defaultdict(list)

        for _, relation_config in self.relations_config(event=event).items():
            chroot = relation_config["chroot"]
            generated_acl = make_acl(
                scheme="sasl",
                credential=relation_config["username"],
                read="r" in relation_config["acl"],
                write="w" in relation_config["acl"],
                create="c" in relation_config["acl"],
                delete="d" in relation_config["acl"],
                admin="a" in relation_config["acl"],
            )

            acls[chroot].append(generated_acl)

        return dict(acls)

    def relations_config_values_for_key(
        self, key: str, event: Optional[RelationEvent] = None
    ) -> Set[str]:
        """Grabs a specific auth config value from all related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Set of all app values matching a specific key from `relations_config()`
        """
        return {config.get(key, "") for config in self.relations_config(event=event).values()}

    def update_acls(self, event: Optional[RelationEvent]) -> None:
        """Compares leader auth config to incoming relation config, applies necessary add/update/remove actions.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        super_password, _ = self.charm.cluster.passwords
        zk = ZooKeeperManager(
            hosts=self.charm.cluster.active_hosts, username="super", password=super_password
        )

        leader_chroots = zk.leader_znodes(path="/")
        logger.info(f"{leader_chroots=}")

        relation_chroots = self.relations_config_values_for_key("chroot", event=event)
        logger.info(f"{relation_chroots=}")

        acls = self.build_acls(event=event)
        logger.info(f"{acls=}")

        # Looks for newly related applications not in config yet
        for chroot in relation_chroots - leader_chroots:
            logger.info(f"CREATE CHROOT - {chroot}")
            zk.create_znode_leader(chroot, acls[chroot])

        # Looks for existing related applications
        for chroot in relation_chroots & leader_chroots:
            logger.info(f"UPDATE CHROOT - {chroot}")
            zk.set_acls_znode_leader(chroot, acls[chroot])

        # Looks for applications no longer in the relation but still in config
        for chroot in leader_chroots - relation_chroots:
            if not self._is_child_of(chroot, relation_chroots):
                logger.info(f"DROP CHROOT - {chroot}")
                zk.delete_znode_leader(chroot)

    @staticmethod
    def _is_child_of(path: str, chroots: Set[str]) -> bool:
        """Checks if given path is a child znode from a set of chroot paths.

        Args:
            path: the desired znode path to check parenthood of
            chroots: the potential parent znode paths

        Returns:
            True if `path` is a child of a znode in `chroots`. Otherwise False.
        """
        for chroot in chroots:
            if path.startswith(chroot.rstrip("/") + "/"):
                return True

        return False

    @staticmethod
    def build_uris(active_hosts: Set[str], chroot: str, client_port: int = 2181) -> List[str]:
        """Builds connection uris for passing to the client relation data.

        Args:
            active_hosts: all ZK hosts in the peer relation
            chroot: the chroot to append to the host IP
            client_port: the client_port to append to the host IP

        Returns:
            List of chroot appended connection uris
        """
        uris = []
        for host in active_hosts:
            uris.append(f"{host}:{client_port}{chroot}")

        return uris

    def _on_client_relation_updated(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_changed` and `client_relation_joined` events.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        if not self.charm.unit.is_leader():
            return

        try:
            self.update_acls(event=event)
        except (
            MembersSyncingError,
            MemberNotReadyError,
            QuorumLeaderNotFoundError,
            KazooTimeoutError,
            UnitNotFoundError,
        ) as e:
            logger.warning(str(e))
            self.charm.unit.status = MaintenanceStatus(str(e))
            return

        return

    def apply_relation_data(self) -> None:
        """Updates relation data with new auth values upon concluded client_relation events."""
        relations_config = self.relations_config()

        for relation_id, config in relations_config.items():
            hosts = self.charm.cluster.active_hosts

            relation_data = {}
            relation_data["username"] = config["username"]
            relation_data["password"] = config["password"] or ZooKeeperCluster.generate_password()
            relation_data["chroot"] = config["chroot"]
            relation_data["endpoints"] = ",".join(list(hosts))
            relation_data["uris"] = ",".join(
                [f"{host}:{self.charm.cluster.client_port}{config['chroot']}" for host in hosts]
            )

            self.app_relation.data[self.charm.app].update({config["username"]: config["password"]})

            self.charm.model.get_relation(REL_NAME, int(relation_id)).data[self.charm.app].update(
                relation_data
            )

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Removes user from ZK app data on `client_relation_departed`.

        Args:
            event: used for passing `RelationBrokenEvent` to subequent methods
        """
        if not self.charm.unit.is_leader():
            return

        # TODO: maybe remove departing app from event relation data?

        config = self.relation_config(relation=event.relation)
        username = config["username"] if config else ""

        if username in self.charm.cluster.relation.data[self.charm.app]:
            logger.info(f"DELETING - {username}")
            del self.charm.cluster.relation.data[self.charm.app][username]

        # call normal updated handler
        self._on_client_relation_updated(event=event)
