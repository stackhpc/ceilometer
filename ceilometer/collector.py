# -*- encoding: utf-8 -*-
#
# Copyright © 2012-2013 eNovance <licensing@enovance.com>
#
# Author: Julien Danjou <julien@danjou.info>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import socket

import msgpack
from oslo.config import cfg

from ceilometer import messaging
from ceilometer.openstack.common.gettextutils import _  # noqa
from ceilometer.openstack.common import log
from ceilometer.openstack.common import service as os_service
from ceilometer.openstack.common import units
from ceilometer import service

OPTS = [
    cfg.StrOpt('udp_address',
               default='0.0.0.0',
               help='Address to which the UDP socket is bound. Set to '
               'an empty string to disable.'),
    cfg.IntOpt('udp_port',
               default=4952,
               help='Port to which the UDP socket is bound.'),
]

cfg.CONF.register_opts(OPTS, group="collector")
cfg.CONF.import_opt('metering_topic', 'ceilometer.publisher.rpc',
                    group="publisher_rpc")


LOG = log.getLogger(__name__)


class CollectorService(service.DispatchedService, os_service.Service):
    """Listener for the collector service."""

    @staticmethod
    def rpc_enabled():
        # cfg.CONF opt from oslo.messaging.transport
        return cfg.CONF.rpc_backend or cfg.CONF.transport_url

    def start(self):
        """Bind the UDP socket and handle incoming data."""
        super(CollectorService, self).start()
        if cfg.CONF.collector.udp_address:
            self.tg.add_thread(self.start_udp)

        if self.rpc_enabled():
            self.rpc_server = messaging.get_rpc_server(
                cfg.CONF.publisher_rpc.metering_topic, self)
            self.rpc_server.start()

            if not cfg.CONF.collector.udp_address:
                # Add a dummy thread to have wait() working
                self.tg.add_timer(604800, lambda: None)

    def start_udp(self):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((cfg.CONF.collector.udp_address,
                  cfg.CONF.collector.udp_port))

        self.udp_run = True
        while self.udp_run:
            # NOTE(jd) Arbitrary limit of 64K because that ought to be
            # enough for anybody.
            data, source = udp.recvfrom(64 * units.Ki)
            try:
                sample = msgpack.loads(data)
            except Exception:
                LOG.warn(_("UDP: Cannot decode data sent by %s"), str(source))
            else:
                try:
                    LOG.debug(_("UDP: Storing %s"), str(sample))
                    self.dispatcher_manager.map_method('record_metering_data',
                                                       sample)
                except Exception:
                    LOG.exception(_("UDP: Unable to store meter"))

    def stop(self):
        self.udp_run = False
        if self.rpc_enabled():
            self.rpc_server.stop()
        super(CollectorService, self).stop()

    def record_metering_data(self, context, data):
        """RPC endpoint for messages we send to ourselves.

        When the notification messages are re-published through the
        RPC publisher, this method receives them for processing.
        """
        self.dispatcher_manager.map_method('record_metering_data', data=data)
