# -*- coding: utf-8 -*-
#
# vmparking.py
#
# Copyright (C) 2010 Antoine Mercadal <antoine.mercadal@inframonde.eu>
# This file is part of ArchipelProject
# http://archipelproject.org
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import os
import random
import shutil
import string

from archipel.archipelHypervisor import TNArchipelHypervisor
from archipel.archipelVirtualMachine import TNArchipelVirtualMachine
from archipelcore.archipelPlugin import TNArchipelPlugin
from archipelcore import xmpp

from archipelcore.utils import build_error_iq, build_error_message

ARCHIPEL_ERROR_CODE_VMPARK_LIST            = -11001
ARCHIPEL_ERROR_CODE_VMPARK_PARK            = -11002
ARCHIPEL_ERROR_CODE_VMPARK_UNPARK          = -11003
ARCHIPEL_ERROR_CODE_VMPARK_DELETE          = -11004
ARCHIPEL_ERROR_CODE_VMPARK_EDIT_DEFINITION = -11004
ARCHIPEL_ERROR_CODE_VMPARK_CREATE_PARKED   = -11005

ARCHIPEL_NS_HYPERVISOR_VMPARKING = "archipel:hypervisor:vmparking"
ARCHIPEL_NS_VM_VMPARKING         = "archipel:vm:vmparking"


class TNVMParking (TNArchipelPlugin):

    def __init__(self, configuration, entity, entry_point_group):
        """
        Initialize the plugin.
        @type configuration: Configuration object
        @param configuration: the configuration
        @type entity: L{TNArchipelEntity}
        @param entity: the entity that owns the plugin
        @type entry_point_group: string
        @param entry_point_group: the group name of plugin entry_point
        """
        TNArchipelPlugin.__init__(self, configuration=configuration, entity=entity, entry_point_group=entry_point_group)

        # creates permissions
        self.entity.permission_center.create_permission("vmparking_park", "Authorizes user to park a virtual machines", False)

        if isinstance(self.entity, TNArchipelHypervisor):
            self.entity.permission_center.create_permission("vmparking_list", "Authorizes user to list virtual machines in parking", False)
            self.entity.permission_center.create_permission("vmparking_unpark", "Authorizes user to unpark a virtual machines", False)
            self.entity.permission_center.create_permission("vmparking_delete", "Authorizes user to delete parked virtual machines", False)
            self.entity.permission_center.create_permission("vmparking_edit_definition", "Authorizes user to edit the xml definition of a parked virtual machines", False)
            self.entity.permission_center.create_permission("vmparking_create_parked", "Authorizes user to create a new VM in parking", False)

        # vocabulary
        if isinstance(self.entity, TNArchipelHypervisor):
            registrar_items = [{"commands": ["park"],
                                "parameters": [{"name": "identifiers", "description": "the UUIDs of the VM to park, separated with comas, with no space"}],
                                "method": self.message_park_hypervisor,
                                "permissions": ["vmparking_park"],
                                "description": "Park the virtual machine with the given UUIDs"},

                               {"commands": ["unpark"],
                                "parameters": [{"name": "identifiers", "description": "UUIDs of the virtual machines or parking tickets, separated by comas, with no space"}],
                                "method": self.message_unpark,
                                "permissions": ["vmparking_unpark"],
                                "description": "Unpark the virtual machine parked with the given identifier"},

                               {"commands": ["park list"],
                                "parameters": [],
                                "method": self.message_list,
                                "permissions": ["vmparking_list"],
                                "description": "List all parked virtual machines"}]

        elif isinstance(self.entity, TNArchipelVirtualMachine):
            registrar_items = [{"commands": ["park"],
                                "parameters": [],
                                "method": self.message_park_vm,
                                "permissions": ["vmparking_park"],
                                "description": "Park the virtual machine"}]

        self.entity.add_message_registrar_items(registrar_items)

    # Plugin interface

    def register_handlers(self):
        """
        This method will be called by the plugin user when it will be
        necessary to register module for listening to stanza.
        """
        if isinstance(self.entity, TNArchipelHypervisor):
            self.entity.xmppclient.RegisterHandler('iq', self.process_iq_for_hypervisor, ns=ARCHIPEL_NS_HYPERVISOR_VMPARKING)
        elif isinstance(self.entity, TNArchipelVirtualMachine):
            self.entity.xmppclient.RegisterHandler('iq', self.process_iq_for_vm, ns=ARCHIPEL_NS_VM_VMPARKING)

    def unregister_handlers(self):
        """
        Unregister the handlers.
        """
        if isinstance(self.entity, TNArchipelHypervisor):
            self.entity.xmppclient.UnregisterHandler('iq', self.process_iq_for_hypervisor, ns=ARCHIPEL_NS_HYPERVISOR_VMPARKING)
        elif isinstance(self.entity, TNArchipelVirtualMachine):
            self.entity.xmppclient.UnregisterHandler('iq', self.process_iq_for_vm, ns=ARCHIPEL_NS_VM_VMPARKING)

    # Database Management

    def get_vms_from_uuid(self, vms, callback):
        """
        Get a list of parked vms from central db based on a list of uuids.
        @type uuids: List
        @param uuid: The list of vm objects like [ { "uuid": x } , { "uuid": y} ]
        """
        uuid_strings = []
        for vm in vms:
            uuid_strings.append(vm["uuid"])
        where_statement = "uuid='" + "' or uuid='".join(uuid_strings) + "' and (hypervisor='None' or hypervisor not in (select jid from hypervisors where status='Online'))"
        self.entity.get_plugin("centraldb").read_vms("*", where_statement, callback)

    def get_vms_from_name(self, name, callback):
        """
        Get a list of parked vms from central db based on name pattern.
        @type name: string
        @param name: The pattern name of vms like vm_
        """
        where_statement = "name like '%s%' and domain != 'None' and (hypervisor='None' or hypervisor not in (select jid from hypervisors where status='Online')) order by name" % name
        self.entity.get_plugin("centraldb").read_vms("*", where_statement, callback)

    def get_vms(self, iq, conn):
        """
        List virtual machines in the park withing an interval
        """
        vms_per_page = 30
        page = iq.getQuery().getTagAttr('archipel', 'page')
        if not page:
            page = 0
        filter = iq.getQuery().getTagAttr('archipel', 'filter')

        def _on_centralagent_reply(vms):
            try:
                self.entity.log.debug("VMPARKING: We got %s entry from central db" % len(vms))
                reply = iq.buildReply("result")
                nodes = []
                for vm in vms:
                    try:
                        vm_node = xmpp.Node("virtualmachine", attrs={"uuid": vm["uuid"], "parker": vm["parker"], "date": vm["creation_date"], 'name':vm['name']})
                        xmldef = xmpp.simplexml.NodeBuilder(vm["domain"]).getDom()
                        xmldef.delChild("description")
                        vm_node.addChild(node=xmldef)
                        nodes.append(vm_node)
                    except:
                        self.entity.log.warning("VMPARKING: Error parsing entry %s" % vm)

                reply.setQueryPayload(nodes)
            except Exception as ex:
                reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_LIST)
            self.entity.xmppclient.send(reply)
            raise xmpp.protocol.NodeProcessed
        where_statement = "domain != 'None' and (hypervisor='None' or hypervisor not in (select jid from hypervisors where status='Online')) order by name limit %s offset %s" % (vms_per_page, vms_per_page * int(page))
        if filter:
            where_statement = "name like '%%%s%%' and %s" % (filter, where_statement)
        self.entity.get_plugin("centraldb").read_vms("*", where_statement, _on_centralagent_reply)

    # Plugin information

    @staticmethod
    def plugin_info():
        """
        Return informations about the plugin.
        @rtype: dict
        @return: dictionary contaning plugin informations
        """
        plugin_friendly_name = "Virtual Machine Parking"
        plugin_identifier = "vmparking"
        plugin_configuration_section = None
        plugin_configuration_tokens = []
        return {"common-name": plugin_friendly_name,
                "identifier": plugin_identifier,
                "configuration-section": plugin_configuration_section,
                "configuration-tokens": plugin_configuration_tokens}

    # Processing function
    def park(self, vm_informations):
        """
        Park a virtual machine.
        @type vm_informations: list
        @param vm_informations: list of dict like {"uuid": x, "parker": z)}
        """
        vm_informations_cleaned = []
        for vm_info in vm_informations:

            vm = self.entity.get_vm_by_uuid(vm_info["uuid"])
            if not vm:
                self.entity.log.warning("VMPARKING: No virtual machine with UUID %s" % vm_info["uuid"])
                continue
            if not vm.domain:
                self.entity.log.warning("VMPARKING: VM with UUID %s cannot be parked because it is not defined" % vm_info["uuid"])
                continue
            vm_informations_cleaned.append(vm_info)

        # Now, perform operations
        new_vm_info = []
        for vm_info in vm_informations_cleaned:
            vm = self.entity.get_vm_by_uuid(vm_info["uuid"])
            if not vm.info()["state"] == 5:
                vm.destroy()
            domain = vm.xmldesc(mask_description=False)
            vm_jid = xmpp.JID(domain.getTag("description").getData().split("::::")[0])
            vm_info["hypervisor"] = None
            vm_info['name'] = domain.getTag("name").getData()
            new_vm_info.append(vm_info)
            self.entity.soft_free(vm_jid)
        if len(new_vm_info) > 0:
            self.entity.get_plugin("centraldb").update_vms(vm_informations)
            self.entity.push_change("vmparking", "parked")

    def unpark(self, vm_information):
        """
        Unpark virtual machine
        @type vm_information: list
        @param vm_information: list of dict like {"uuid": x, "start": True|False, "parker": z}
        """

        def _unpark_callback(vm_items):

            vm_information_by_uuid  = {}

            for vm_info in vm_information:
                vm_information_by_uuid[vm_info["uuid"]] = vm_info

            for vm_item in vm_items:
                vm_info = vm_information_by_uuid[vm_item["uuid"]]
                domain = vm_item["domain"]
                ret = str(domain).replace('xmlns=\"archipel:hypervisor:vmparking\"', '')
                domain = xmpp.simplexml.NodeBuilder(data=ret).getDom()
                vmjid = domain.getTag("description").getData().split("::::")[0]
                vmpass = domain.getTag("description").getData().split("::::")[1]
                vmname = domain.getTag("name").getData()
                self.entity.log.debug("VMPARKING: about to create vm thread")
                vm_thread = self.entity.soft_alloc(xmpp.JID(vmjid), vmname, vmpass, start=False, organization_info=self.entity.vcard_infos)
                vm = vm_thread.get_instance()
                vm.register_hook("HOOK_ARCHIPELENTITY_XMPP_AUTHENTICATED", method=vm.define_hook, user_info=domain, oneshot=True)
                if vm_info["start"]:
                    vm.register_hook("HOOK_ARCHIPELENTITY_XMPP_AUTHENTICATED", method=vm.control_create_hook, oneshot=True)
                vm_thread.start()

                self.entity.push_change("vmparking", "unparked")
                self.entity.log.info("VMPARKING: successfully unparked %s" % str(vmjid))

        self.get_vms_from_uuid(vm_information, _unpark_callback)

    def delete(self, vms_uuids):
        """
        Delete a parked virtual machine
        @type vm_uuids: list
        @param uuid: list of dic like {"uuid": x}
        """

        def _unregister_vms_callback(unregistered_vms):
            self.entity.log.debug("VMPARKING: unregistered_vms: %s" % unregistered_vms)
            # Then perfom cleanup operations
            jids = []

            for vm in unregistered_vms:
                vmfolder = "%s/%s" % (self.configuration.get("VIRTUALMACHINE", "vm_base_path"), vm["uuid"])

                if os.path.exists(vmfolder):
                    shutil.rmtree(vmfolder)

                jids.append(xmpp.JID(vm["jid"]))

            # And remove the XMPP account
            self.entity.get_plugin("xmppserver").users_unregister(jids)
            self.entity.log.info("VMPARKING: successfully deleted %s from parking" % str(unregistered_vms))
            self.entity.push_change("vmparking", "deleted")

        # Update DB and Push
        self.entity.get_plugin("centraldb").unregister_vms(vms_uuids, _unregister_vms_callback)

    def get_definition(self, uuid):
        """
        Retrive the xml vm defintion form the parking
        """
        pass

    def edit_definition(self, iq, uuid, domain):
        """
        Update the domain XML of a parked VM
        @type uuid: String
        @param uuid: the VM UUID
        @type domain: xmpp.Node
        @param domain: the new XML description
        """
        def _on_centralagent_reply(results):
            try:
                error = False
                result_msg = ""
                for result in results:

                    result_msg += "uuid : %s, error : %s, msg : %s" % (result["uuid"], result["error"], result["result"])
                    if result["error"] == "True":
                        error = True

                if not error:
                    reply = iq.buildReply("result")
                    self.entity.push_change("vmparking", "updated")
                else:
                    reply = build_error_iq(self, result_msg, iq, ARCHIPEL_ERROR_CODE_VMPARK_EDIT_DEFINITION)

            except Exception as ex:
                reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_EDIT_DEFINITION)

            self.entity.xmppclient.send(reply)
            raise xmpp.protocol.NodeProcessed

        self.entity.get_plugin("centraldb").update_vms_domain([{"uuid":uuid, "domain":domain}], _on_centralagent_reply)

    def create_parked(self, vm_informations):
        """
        Creates a VM directly into the parking.
        @type vm_informations: list
        @param vm_informations: list containing VM to park [{"uuid": x, domain: y, parker: x, creation_date: d, status: s}]
        """
        for vm_info in vm_informations:

            vm = self.entity.get_vm_by_uuid(vm_info["uuid"])
            if vm:
                raise Exception("There is already a VM with UUID %s" % vm_info["uuid"])

            if vm_info["domain"].getTag("description"):
                raise Exception("You cannot park a VM XML with a <description/> tag. Please remove it")

            password = ''.join([random.choice(string.letters + string.digits) for i in range(32)])
            vm_info["domain"].addChild("description").setData("%s@%s::::%s" % (vm_info["uuid"], self.entity.jid.getDomain(), password))
            vm_info["domain"] = str(vm_info["domain"]).replace('xmlns=\"archipel:hypervisor:vmparking\"', '')

        self.entity.get_plugin("centraldb").register_vms(vm_informations)
        self.entity.push_change("vmparking", "parked")

    # XMPP Management for hypervisors

    def process_iq_for_hypervisor(self, conn, iq):
        """
        This method is invoked when a ARCHIPEL_NS_HYPERVISOR_VMPARKING IQ is received.
        It understands IQ of type:
            - list
            - park
            - create_parked
            - unpark
            - destroy
            - edit_definition
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        reply = None
        action = self.entity.check_acp(conn, iq)
        self.entity.check_perm(conn, iq, action, -1, prefix="vmparking_")
        if action == "list":
            reply = self.iq_list(iq, conn)
        if action == "park":
            reply = self.iq_park(iq)
        if action == "unpark":
            reply = self.iq_unpark(iq)
        if action == "delete":
            reply = self.iq_delete(iq)
        if action == "edit_definition":
            reply = self.iq_edit_definition(iq)
        if action == "create_parked":
            reply = self.iq_create_parked(iq)
        if reply:
            conn.send(reply)
        raise xmpp.protocol.NodeProcessed

    def iq_list(self, iq, conn):
        """
        Return the list of parked virtual machines
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = self.get_vms(iq, conn)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_LIST)
        return reply

    #FIXME THIS IS BROKEN
    def message_list(self, msg):
        """
        Handle the parking list message.
        @type msg: xmmp.Protocol.Message
        @param msg: the message
        @rtype: string
        @return: the answer
        """
        try:
            tokens = msg.getBody().split()
            if not len(tokens) == 2:
                return "I'm sorry, you use a wrong format. You can type 'help' to get help."
            parked_vms = self.get_vms()
            resp = "Sure! Here is the virtual machines parked:\n"
            for info in parked_vms:
                ticket = info["info"]["itemid"]
                name = info["domain"].getTag("name").getData()
                uuid = info["domain"].getTag("uuid").getData()
                resp = "%s - [%s]: %s (%s)\n" % (resp, ticket, name, uuid)
            return resp

        except Exception as ex:
            return build_error_message(self, ex, msg)

    def iq_park(self, iq):
        """
        Park virtual machine
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            items = iq.getTag("query").getTag("archipel").getTags("item")
            vms_info = []
            for item in items:
                vm_uuid = item.getAttr("uuid")
                if not vm_uuid:
                    self.entity.log.error("VMPARKING: Unable to park vm: missing 'uuid' element.")
                    raise Exception("You must must set the UUID of the vms you want to park")

                vms_info.append({"uuid": vm_uuid, "parker": str(iq.getFrom())})

            self.park(vms_info)

            reply = iq.buildReply("result")
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_PARK)
        return reply

    def message_park_hypervisor(self, msg):
        """
        Handle the park message.
        @type msg: xmmp.Protocol.Message
        @param msg: the message
        @rtype: string
        @return: the answer
        """
        try:
            tokens = msg.getBody().split()
            if len(tokens) < 2:
                return "I'm sorry, you use a wrong format. You can type 'help' to get help."
            uuids = tokens[1].split(",")
            vms_info = []
            for vmuuid in uuids:
                vms_info.append({"uuid": vmuuid, "hypervisor": "None", "parker": str(msg.getFrom())})

            self.park(vms_info)

            if len(uuids) == 1:
                return "Virtual machine is parking."
            else:
                return "Virtual machines are parking."

        except Exception as ex:
            return build_error_message(self, ex, msg)

    def iq_unpark(self, iq):
        """
        Unpark virtual machine
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            items = iq.getTag("query").getTag("archipel").getTags("item")
            vms_info = []
            for item in items:
                identifier = item.getAttr("identifier")
                autostart = False
                if item.getAttr("start") and item.getAttr("start").lower() in ("yes", "y", "true", "1"):
                    autostart = True
                if not self.entity.get_vm_by_uuid(identifier):
                    vms_info.append({"uuid": identifier, "start": autostart, "parker": str(iq.getFrom())})

            if vms_info:
                self.unpark(vms_info)

        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_UNPARK)

        return reply

    def message_unpark(self, msg):
        """
        Handle the unpark message.
        @type msg: xmmp.Protocol.Message
        @param msg: the message
        @rtype: string
        @return: the answer
        """
        try:
            tokens = msg.getBody().split()
            if len(tokens) < 2:
                return "I'm sorry, you use a wrong format. You can type 'help' to get help."
            itemids = tokens[1].split(",")
            vms_info = []
            for itemid in itemids:
                vms_info.append({"uuid": itemid, "start": False, "parker": str(msg.getFrom())})

            self.unpark(vms_info)

            if len(itemids) == 1:
                return "Virtual machine is unparking."
            else:
                return "Virtual machines are unparking."
        except Exception as ex:
            return build_error_message(self, ex, msg)

    def iq_delete(self, iq):
        """
        Delete a parked virtual machine
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            items = iq.getTag("query").getTag("archipel").getTags("item")
            vm_uuids = []

            for item in items:
                vm_uuids.append({"uuid": item.getAttr("identifier")})

            self.delete(vm_uuids)

        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_DELETE)
        return reply

    def iq_edit_definition(self, iq):
        """
        Update the XML description of a parked virtual machine
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            identifier = iq.getTag("query").getTag("archipel").getAttr("identifier")
            domain = iq.getTag("query").getTag("archipel").getTag("domain")
            reply = self.edit_definition(iq, identifier, domain)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_EDIT_DEFINITION)
        return reply

    def iq_create_parked(self, iq):
        """
        Create a VM in directly into the parking
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            items = iq.getTag("query").getTag("archipel").getTags("item")
            vms_info = []
            for item in items:
                vm_uuid = item.getAttr("uuid")
                if not vm_uuid:
                    self.entity.log.error("VMPARKING: Unable to park vm: missing 'uuid' element.")
                    raise Exception("You must must set the UUID of the vms you want to park")
                vm_domain = item.getTag("domain")
                vms_info.append({"uuid": vm_uuid, "domain": vm_domain, "parker": str(iq.getFrom()), "creation_date": datetime.datetime.now(), "hypervisor": "None"})

            self.create_parked(vms_info)

            reply = iq.buildReply("result")

        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_CREATE_PARKED)
        return reply

    # XMPP Management for hypervisors

    def process_iq_for_vm(self, conn, iq):
        """
        This method is invoked when a ARCHIPEL_NS_VM_VMPARKING IQ is received.
        It understands IQ of type:
            - park
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        reply = None
        action = self.entity.check_acp(conn, iq)
        self.entity.check_perm(conn, iq, action, -1, prefix="vmparking_")
        if action == "park":
            reply = self.iq_park_vm(iq)
        if reply:
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed

    def iq_park_vm(self, iq):
        """
        ask own hypervisor to park the virtual machine
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            vms_info = [{"uuid": self.entity.uuid, "parker": str(iq.getFrom())}]
            self.entity.hypervisor.get_plugin("vmparking").park(vms_info)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_VMPARK_PARK)
        return reply

    def message_park_vm(self, msg):
        """
        Handle the park message for vm.
        @type msg: xmmp.Protocol.Message
        @param msg: the message
        @rtype: string
        @return: the answer
        """
        try:
            tokens = msg.getBody().split()
            if not len(tokens) == 1:
                return "I'm sorry, you use a wrong format. You can type 'help' to get help."
            vms_info = [{"uuid": self.entity.uuid, "parker": str(msg.getFrom())}]
            self.entity.hypervisor.get_plugin("vmparking").park(vms_info)
            return "I'm parking."
        except Exception as ex:
            return build_error_message(self, ex, msg)
